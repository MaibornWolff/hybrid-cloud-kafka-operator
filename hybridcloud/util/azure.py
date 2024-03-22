from azure.identity import DefaultAzureCredential
from azure.mgmt.eventhub import EventHubManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.privatedns import PrivateDnsManagementClient
from hybridcloud_core.configuration import get_one_of


def _subscription_id():
    return get_one_of("backends.azureeventhub.subscription_id", "backends.azure.subscription_id", fail_if_missing=True)


def _credentials():
    return DefaultAzureCredential()


def eventhub_client() -> EventHubManagementClient:
    return EventHubManagementClient(_credentials(), _subscription_id())


def network_client():
    return NetworkManagementClient(_credentials(), _subscription_id())


def privatedns_client():
    return PrivateDnsManagementClient(_credentials(), _subscription_id())
