from datetime import datetime, timezone
import kopf
from .routing import kafka_backend
from hybridcloud_core.configuration import config_get
from hybridcloud_core.operator.reconcile_helpers import ignore_control_label_change, process_action_label
from hybridcloud_core.k8s.api import get_namespaced_custom_object, patch_namespaced_custom_object_status, create_or_update_secret, get_secret, delete_secret
from ..util import k8s
from ..util.constants import BACKOFF


if config_get("handler_on_resume", default=False):
    @kopf.on.resume(*k8s.KafkaTopicUser.kopf_on(), backoff=BACKOFF)
    async def user_resume(spec, meta, labels, name, namespace, body, status, retry, diff, logger, **kwargs):
        await user_manage(spec, meta, labels, name, namespace, body, status, retry, diff, logger, **kwargs)


@kopf.on.create(*k8s.KafkaTopicUser.kopf_on(), backoff=BACKOFF)
@kopf.on.update(*k8s.KafkaTopicUser.kopf_on(), backoff=BACKOFF)
async def user_manage(spec, meta, labels, name, namespace, body, status, retry, diff, logger, **kwargs):
    if ignore_control_label_change(diff):
        logger.debug("Only control labels removed. Nothing to do.")
        return

    # Wait for topic
    topic_namespace = spec["topicRef"].get("namespace", namespace)
    topic_name = spec["topicRef"]["name"]

    topic_object = get_namespaced_custom_object(k8s.KafkaTopic, topic_namespace, topic_name)
    if not topic_object:
        _status(name, namespace, status, "waiting")
        raise kopf.TemporaryError("Waiting for topic object to be created.", delay=10 if retry < 5 else 20 if retry < 10 else 30)

    status = topic_object.get("status")
    if not status or not "topic_info" in status:
        _status(name, namespace, status, "waiting")
        raise kopf.TemporaryError("Waiting for topic to be created by backend.", delay=10 if retry < 5 else 20 if retry < 10 else 30)
    topic_info = status["topic_info"]
    broker_info = status["broker_info"]
    backend_name = status.get("backend", topic_object.get("spec", dict()).get("backend", config_get("backend", fail_if_missing=True)))
    backend = kafka_backend(backend_name, logger)

    if not backend.topic_exists(topic_namespace, topic_name, broker_info):
        _status(name, namespace, status, "waiting")
        raise kopf.TemporaryError("Waiting for topic to be finished creating by backend.", delay=10 if retry < 5 else 20 if retry < 10 else 30)

    # Validate spec
    valid, reason = backend.user_spec_valid(namespace, name, spec, topic_info, broker_info)
    if not valid:
        _status(name, namespace, status, "failed", f"Validation failed: {reason}")
        raise kopf.PermanentError("Spec is invalid, check status for details")

    _status(name, namespace, status, "working", backend=backend_name, broker_info=broker_info, topic_info=topic_info)

    # Create user
    logger.info("Starting create/update of user")

    reset_credentials = False

    def action_reset_credentials():
        nonlocal reset_credentials
        reset_credentials = True
        return "Credentials reset"
    process_action_label(labels, {
        "reset-credentials": action_reset_credentials,
    }, body, k8s.KafkaTopicUser)

    credentials = await backend.create_or_update_user(namespace, name, spec, topic_info, broker_info, reset_credentials=reset_credentials)
    create_or_update_secret(namespace, spec["credentialsSecret"], credentials)

    # mark success
    _status(name, namespace, status, "finished", "User created", backend=backend_name, broker_info=broker_info, topic_info=topic_info)


@kopf.on.delete(*k8s.KafkaTopicUser.kopf_on(), backoff=BACKOFF)
async def user_delete(spec, status, name, namespace, logger, **kwargs):
    if status and "backend" in status:
        backend_name = status["backend"]
    else:
        backend_name = config_get("backend", fail_if_missing=True)
    backend = kafka_backend(backend_name, logger)

    delete_secret(namespace, spec["credentialsSecret"])

    if not status or not "broker_info" in status:
        logger.warn("Could not delete user as no broker information was stored in status")
        return
    broker_info = status["broker_info"]
    topic_info = status["topic_info"]

    await backend.delete_user(namespace, name, topic_info, broker_info)


def _status(name, namespace, status_obj, status, reason=None, backend=None, broker_info=None, topic_info=None):
    if status_obj:
        new_status = dict()
        for k, v in status_obj.items():
            new_status[k] = v
        status_obj = new_status
    else:
        status_obj = dict()
    if backend:
        status_obj["backend"] = backend
    if broker_info:
        status_obj["broker_info"] = broker_info
    if topic_info:
        status_obj["topic_info"] = topic_info
    status_obj["deployment"] = {
        "status": status,
        "reason": reason,
        "latest-update": datetime.now(tz=timezone.utc).isoformat()
    }
    patch_namespaced_custom_object_status(k8s.KafkaTopicUser, namespace, name, status_obj)
