import base64
import time
from ..util.k8s import StrimziKafka, StrimziKafkaTopic, StrimziKafkaUser
from hybridcloud_core.configuration import get_one_of
from hybridcloud_core.operator.reconcile_helpers import field_from_spec
from hybridcloud_core.k8s.api import get_namespaced_custom_object, create_or_update_namespaced_custom_object, delete_namespaced_custom_object, get_secret
import kopf
import asyncio

def _backend_config(key, default=None, fail_if_missing=False):
    return get_one_of(f"backends.strimzi.{key}", default=default, fail_if_missing=fail_if_missing)


DEFAULT_KAFKA_CONFIG = {
    "offsets.topic.replication.factor": 1,
    "transaction.state.log.replication.factor": 1,
    "transaction.state.log.min.isr": 1,
    "default.replication.factor": 1,
    "min.insync.replicas": 1,
    "inter.broker.protocol.version": "3.2"
}


class StrimziBackend:
    def __init__(self, logger):
        self._logger = logger

    def broker_spec_valid(self, namespace, name, spec):
        # Check if class is defined
        size_class = field_from_spec(spec, "size.class", default=None)
        if size_class:
            class_def = _backend_config(f"broker.classes.{size_class}")
            if not class_def:
                return (False, f"Size class {size_class} is not configured for the operator")
        return (True, "")

    def broker_exists(self, namespace, name):
        return get_namespaced_custom_object(StrimziKafka, namespace, name) is not None

    async def create_or_update_broker(self, namespace, name, spec, extra_tags=None):
        size_class = field_from_spec(spec, "size.class", default=None)
        if size_class:
            class_def = _backend_config(f"broker.classes.{size_class}", default={})
        else:
            class_def = {}
        broker_spec = _generate_kafka_spec(namespace, name, spec, class_def)
        create_or_update_namespaced_custom_object(StrimziKafka, namespace, name, broker_spec)
        # Wait for broker to be ready
        broker_obj = await _wait_for_broker(namespace, name)
        # Extract listener address
        for listener in broker_obj["status"].get("listeners", list()):
            if listener.get("name") == "tls":
                return {
                    "bootstrapServers": listener["bootstrapServers"],
                    "name": name,
                    "namespace": namespace,
                }
        raise kopf.TemporaryError("Could not determine bootstrap server address of broker", delay=30)

    async def delete_broker(self, namespace, name):
        delete_namespaced_custom_object(StrimziKafka, namespace, name)

    def topic_spec_valid(self, namespace, name, spec, broker_info):
        return (True, "")

    def topic_exists(self, namespace, name, broker_info):
        return get_namespaced_custom_object(StrimziKafkaTopic, namespace, name) is not None

    async def create_or_update_topic(self, namespace, name, spec, broker_info):
        topic_spec = _generate_topic_spec(namespace, name, spec, broker_info)
        create_or_update_namespaced_custom_object(StrimziKafkaTopic, namespace, name, topic_spec)
        await _wait_for_topic(namespace, name)
        return {
            "name": name,
            "namespace": namespace
        }

    async def delete_topic(self, namespace, name, broker_info):
        delete_namespaced_custom_object(StrimziKafkaTopic, namespace, name)

    def user_spec_valid(self, namespace, name, spec, topic_info, broker_info):
        if name == "owner":
            return (False, "Username 'owner' is reserved")
        return (True, "")

    def user_exists(self, namespace, name, topic_info, broker_info):
        return get_namespaced_custom_object(StrimziKafkaUser, namespace, name) is not None

    async def create_or_update_user(self, namespace, name, spec, topic_info, broker_info, reset_credentials=False):
        permissions = spec["permissions"]
        return await self._create_or_update_user(namespace, name, permissions, topic_info, broker_info, reset_credentials=reset_credentials)

    async def create_or_update_topic_credentials(self, topic_info, broker_info, reset_credentials=False):
        namespace = topic_info["namespace"]
        name = topic_info["name"]+"-owner"
        permissions = {"consume": True, "produce": True}
        return await self._create_or_update_user(namespace, name, permissions, topic_info, broker_info, reset_credentials=reset_credentials)

    async def _create_or_update_user(self, namespace, name, permissions, topic_info, broker_info, reset_credentials=False):
        user_spec = _generate_user_spec(namespace, name, permissions, topic_info, broker_info)
        create_or_update_namespaced_custom_object(StrimziKafkaUser, namespace, name, user_spec)
        user_obj = await _wait_for_user(namespace, name)
        if not "username" in user_obj["status"] or not "secret" in user_obj["status"]:
            raise kopf.TemporaryError("Could not determine credentials for user", delay=10)
        secret_name = user_obj["status"]["secret"]
        secret = get_secret(namespace, secret_name)
        return {
            "username": user_obj["status"]["username"],
            "password": base64.b64decode(secret.data["password"]).decode("utf-8"),
            "bootstrap_servers": broker_info["bootstrapServers"],
            "security_protocol": "SASL_SSL",
            "sasl_mechanism": "SCRAM-SHA-256",
            "topic": topic_info["name"],
            "sasl.jaas.config": base64.b64decode(secret.data["sasl.jaas.config"]).decode("utf-8")
        }

    async def delete_user(self, namespace, name, topic_info, broker_info):
        self._delete_user(namespace, name)

    async def delete_topic_credentials(self, topic_info, broker_info):
        namespace = topic_info["namespace"]
        name = topic_info["name"]+"-owner"
        self._delete_user(namespace, name)

    def _delete_user(self, namespace, name):
        delete_namespaced_custom_object(StrimziKafkaUser, namespace, name)


def _generate_kafka_spec(namespace, name, spec, class_def):
    kafka_storage = class_def.get("kafka_storage", {"type": "ephemeral"})
    zookeeper_storage = class_def.get("zookeeper_storage", {"type": "ephemeral"})
    config = class_def.get("kafka_config", dict())
    for k, v in DEFAULT_KAFKA_CONFIG.items():
        if k not in config:
            config[k] = v

    return {
        "apiVersion": "kafka.strimzi.io/v1beta2",
        "kind": "Kafka",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "owner": "hybrid-cloud-kafka-operator"
            }
        },
        "spec": {
            "kafka": {
                "version": class_def.get("version", "3.2.3"),
                "replicas": class_def.get("kafka_replicas", 1),
                "authorization": {
                    "type": "simple",
                },
                "listeners": [
                    {
                        "name": "tls",
                        "port": 9093,
                        "type": "internal",
                        "tls": True,
                        "authentication": {
                            "type": "scram-sha-512"
                        }
                    }
                ],
                "config": config,
                "storage": kafka_storage,
            },
            "zookeeper": {
                "replicas": class_def.get("zookeeper_replicas", 3),
                "storage": zookeeper_storage,
            },
            "entityOperator": {
                "topicOperator": {},
                "userOperator": {}
            }
        }
    }

def _generate_topic_spec(namespace, name, spec, broker_info):
    return {
        "apiVersion": "kafka.strimzi.io/v1beta2",
        "kind": "KafkaTopic",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "strimzi.io/cluster": broker_info["name"]
            }
        },
        "spec": {
            "partitions": field_from_spec(spec, "kafka.partitions", default=1),
            "replicas": 1,
            "config": {
                "retention.ms": field_from_spec(spec, "kafka.retentionInDays", default=1)*1000*60*60*24,
            }
        }
    }


def _acl(topic_name, operation):
    return {
        "resource": {
            "type": "topic",
            "name": topic_name,
            "patternType": "literal"
        },
        "operation": operation,
        "host": "*"
    }


def _generate_user_spec(namespace, name, permissions, topic_info, broker_info):
    topic_name = topic_info["name"]
    acls = [_acl(topic_name, "Describe")]
    if permissions.get("consume", False):
        acls.append(_acl(topic_name, "Read"))
    if permissions.get("produce", False):
        acls.append(_acl(topic_name, "Write"))

    return {
        "apiVersion": "kafka.strimzi.io/v1beta2",
        "kind": "KafkaUser",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "strimzi.io/cluster": broker_info["name"]
            }
        },
        "spec": {
            "authentication": {
                "type": "scram-sha-512"
            },
            "authorization": {
                "type": "simple",
                "acls": acls
            }
        }
    }


async def _wait_for_broker(namespace, name):
    wait_time = 0
    while wait_time < 5*60:
        broker_obj = get_namespaced_custom_object(StrimziKafka, namespace, name)
        if broker_obj and "status" in broker_obj:
            for condition in broker_obj["status"].get("conditions", list()):
                if condition.get("type") == "Ready" and condition.get("status") == "True":
                    return broker_obj
        wait_time += 15
        await asyncio.sleep(15)
    raise kopf.TemporaryError("Strimzi is taking too long to provision the kafka broker", delay=30)


async def _wait_for_topic(namespace, name):
    wait_time = 0
    while wait_time < 1*60:
        topic_obj = get_namespaced_custom_object(StrimziKafkaTopic, namespace, name)
        if topic_obj and "status" in topic_obj:
            for condition in topic_obj["status"].get("conditions", list()):
                if condition.get("type") == "Ready" and condition.get("status") == "True":
                    return topic_obj
        wait_time += 5
        await asyncio.sleep(5)
    raise kopf.TemporaryError("Strimzi is taking too long to provision the kafka topic", delay=30)


async def _wait_for_user(namespace, name):
    wait_time = 0
    while wait_time < 1*60:
        topic_obj = get_namespaced_custom_object(StrimziKafkaUser, namespace, name)
        if topic_obj and "status" in topic_obj:
            for condition in topic_obj["status"].get("conditions", list()):
                if condition.get("type") == "Ready" and condition.get("status") == "True":
                    return topic_obj
        wait_time += 5
        await asyncio.sleep(5)
    raise kopf.TemporaryError("Strimzi is taking too long to provision the kafka user", delay=30)
