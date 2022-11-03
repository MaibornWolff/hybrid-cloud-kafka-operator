from azure.identity import DefaultAzureCredential
from azure.mgmt.eventhub import EventHubManagementClient
from hybridcloud_core.configuration import get_one_of


def _subscription_id():
    return get_one_of("backends.azureservicebus.subscription_id", "backends.azure.subscription_id", fail_if_missing=True)


def _credentials():
    return DefaultAzureCredential()


def eventhub_client() -> EventHubManagementClient:
    return EventHubManagementClient(_credentials(), _subscription_id())
