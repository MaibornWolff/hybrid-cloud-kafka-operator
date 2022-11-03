import string
from azure.core.exceptions import ResourceNotFoundError
from azure.mgmt.eventhub.models import Eventhub, AuthorizationRule, AccessRights, RegenerateAccessKeyParameters, CheckNameAvailabilityParameter, EHNamespace, Sku
from hybridcloud_core.configuration import get_one_of
from hybridcloud_core.operator.reconcile_helpers import field_from_spec
from ..util.azure import eventhub_client


ALLOWED_NAMESPACE_NAME_CHARACTERS = string.ascii_lowercase + string.digits + "-"
TAG_PREFIX = "hybridcloud-kafka-operator"


def _backend_config(key, default=None, fail_if_missing=False):
    return get_one_of(f"backends.azureeventhub.{key}", f"backends.azure.{key}", default=default, fail_if_missing=fail_if_missing)


def _calc_namespace_name(namespace, name):
    return _backend_config("broker.name_pattern", fail_if_missing=True).format(namespace=namespace, name=name).lower()


def _calc_topic_name(namespace, name):
    return _backend_config("topic.name_pattern", default="{namespace}-{name}").format(namespace=namespace, name=name).lower()


def _calc_rule_name(namespace, name):
    return _backend_config("user.name_pattern", default="{namespace}-{name}").format(namespace=namespace, name=name).lower()



class AzureEventHubBackend:
    def __init__(self, logger):
        self._logger = logger
        self._eventhub_client = eventhub_client()
        self._subscription_id = _backend_config("subscription_id", fail_if_missing=True)
        self._location = _backend_config("location", fail_if_missing=True)
        self._resource_group = _backend_config("resource_group", fail_if_missing=True)

    def broker_spec_valid(self, namespace, name, spec):
        broker_name = _calc_namespace_name(namespace, name)
        for char in broker_name:
            if char not in ALLOWED_NAMESPACE_NAME_CHARACTERS:
                return (False, f"Character '{char}' is not allowed in name. Allowed are: letters, digits and hyphens")
        # Check if class is defined
        size_class = field_from_spec(spec, "size.class", default=None)
        if size_class:
            class_def = _backend_config(f"broker.classes.{size_class}")
            if not class_def:
                return (False, f"Size class {size_class} is not configured for the operator")
        # Check if name is available
        if not self.broker_exists(namespace, name):
            result = self._eventhub_client.namespaces.check_name_availability(CheckNameAvailabilityParameter(name=broker_name))
            if not result.name_available:
                return (False, f"Name for eventhub namespace cannot be used: {result.reason}: {result.message}")
        return (True, "")

    def broker_exists(self, namespace, name):
        try:
            return self._eventhub_client.namespaces.get(self._resource_group, _calc_namespace_name(namespace, name))
        except ResourceNotFoundError:
            return False

    async def create_or_update_broker(self, namespace, name, spec, extra_tags=None):
        broker_name = _calc_namespace_name(namespace, name)
        sku = _backend_config("broker.default_sku", default="Basic")
        capacity = int(_backend_config("broker.default_capacity", default=1))
        size_class = field_from_spec(spec, "size.class", default=None)
        if size_class:
            class_def = _backend_config(f"broker.classes.{size_class}", default={})
            sku = class_def.get("sku", sku)
            capacity = class_def.get("capacity", capacity)
        parameters = EHNamespace(
            location=self._location,
            tags=_tags(namespace, name, extra_tags),
            sku=Sku(name=sku, tier=sku, capacity=capacity)
        )
        result = self._eventhub_client.namespaces.begin_create_or_update(self._resource_group, broker_name, parameters).result()
        return {
            "azure_name": broker_name,
            "name": name,
            "namespace": namespace
        }

    async def delete_broker(self, namespace, name):
        broker_name = _calc_namespace_name(namespace, name)
        fake_delete = _backend_config("broker.fake_delete", default=False)
        if fake_delete:
            self.create_or_update_broker(namespace, name, None, {"marked-for-deletion": "yes"})
        else:
            self._eventhub_client.namespaces.begin_delete(self._resource_group, broker_name).result()

    def topic_spec_valid(self, namespace, name, spec, broker_info):
        topic_name = _calc_topic_name(namespace, name)
        for char in topic_name:
            if char not in ALLOWED_NAMESPACE_NAME_CHARACTERS:
                return (False, f"Character '{char}' is not allowed in name. Allowed are: letters, digits and hyphens")
        return (True, "")

    def topic_exists(self, namespace, name, broker_info):
        topic_name = _calc_topic_name(namespace, name)
        try:
            return self._eventhub_client.event_hubs.get(self._resource_group, broker_info["azure_name"], topic_name)
        except ResourceNotFoundError:
            return False

    async def create_or_update_topic(self, namespace, name, spec, broker_info):
        topic_name = _calc_topic_name(namespace, name)
        parameters = Eventhub(
            partition_count=field_from_spec(spec, "kafka.partitions", default=1),
            message_retention_in_days=field_from_spec(spec, "kafka.retentionInDays", default=1)
        )
        eventhub = self._eventhub_client.event_hubs.create_or_update(self._resource_group, broker_info["azure_name"], topic_name, parameters)
        return {
            "azure_name": topic_name
        }

    async def delete_topic(self, namespace, name, broker_info):
        topic_name = _calc_topic_name(namespace, name)
        try:
            self._eventhub_client.event_hubs.delete(self._resource_group, broker_info["azure_name"], topic_name)
        except ResourceNotFoundError:
            pass

    def user_spec_valid(self, namespace, name, spec, topic_info, broker_info):
        rule_name = _calc_rule_name(namespace, name)
        for char in rule_name:
            if char not in ALLOWED_NAMESPACE_NAME_CHARACTERS:
                return (False, f"Character '{char}' is not allowed in name. Allowed are: letters, digits and hyphens")
        return (True, "")

    def user_exists(self, namespace, name, topic_info, broker_info):
        rule_name = _calc_rule_name(namespace, name)
        try:
            return self._eventhub_client.event_hubs.get_authorization_rule(self._resource_group, broker_info["azure_name"], topic_info["azure_name"], rule_name)
        except ResourceNotFoundError:
            return False

    async def create_or_update_user(self, namespace, name, spec, topic_info, broker_info, reset_credentials=False):
        rule_name = _calc_rule_name(namespace, name)
        permissions = spec["permissions"]
        return self._create_or_update_rule(rule_name, topic_info, broker_info, permissions, reset_credentials=reset_credentials)

    async def create_or_update_topic_credentials(self, topic_info, broker_info, reset_credentials=False):
        return self._create_or_update_rule("owner", topic_info, broker_info, {"consume": True, "produce": True}, reset_credentials=reset_credentials)

    def _create_or_update_rule(self, rule_name, topic_info, broker_info, permissions=None, reset_credentials=False):
        parameters = AuthorizationRule(
            rights=_determine_permissions(permissions)
        )
        rule = self._eventhub_client.event_hubs.create_or_update_authorization_rule(self._resource_group, broker_info["azure_name"], topic_info["azure_name"], rule_name, parameters)
        keys = self._eventhub_client.event_hubs.list_keys(self._resource_group, broker_info["azure_name"], topic_info["azure_name"], rule_name)
        if reset_credentials:
            parameters = RegenerateAccessKeyParameters(key_type="PrimaryKey")
            keys = self._eventhub_client.event_hubs.regenerate_keys(self._resource_group, broker_info["azure_name"], topic_info["azure_name"], rule_name, parameters)
        return {
            "username": "$ConnectionString",
            "password": keys.primary_connection_string,
            "bootstrap_servers": f"{broker_info['azure_name']}.servicebus.windows.net:9093",
            "security_protocol": "SASL_SSL",
            "sasl_mechanism": "PLAIN",
            "topic": topic_info["azure_name"],
        }

    async def delete_user(self, namespace, name, topic_info, broker_info):
        rule_name = _calc_rule_name(namespace, name)
        self._delete_rule(rule_name, topic_info, broker_info) 

    async def delete_topic_credentials(self, topic_info, broker_info):
        rule_name = "owner"
        self._delete_rule(rule_name, topic_info, broker_info)

    def _delete_rule(self, rule_name, topic_info, broker_info):
        try:
            self._eventhub_client.event_hubs.delete_authorization_rule(self._resource_group, broker_info["azure_name"], topic_info["azure_name"], rule_name)
        except ResourceNotFoundError as e:
            pass


def _determine_permissions(permissions):
    rights = []
    if permissions.get("consume", False):
        rights.append(AccessRights.LISTEN)
    if permissions.get("produce", False):
        rights.append(AccessRights.SEND)
    return rights


def _tags(namespace, name, extra_tags=None):
    tags = {f"{TAG_PREFIX}:namespace": namespace, f"{TAG_PREFIX}:name": name}
    for k, v in _backend_config("tags", default={}).items():
        tags[k] = v.format(namespace=namespace, name=name)
    if extra_tags:
        for k, v in extra_tags.items():
            tags[f"{TAG_PREFIX}:{k}"] = v
    return tags
