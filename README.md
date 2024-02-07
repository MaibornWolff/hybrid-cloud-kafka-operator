# Hybrid Cloud Operator for Kafka

The Hybrid Cloud Operator for Kafka is a Kubernetes Operator that has been designed for hybrid cloud, multi-teams kubernetes platforms to allow users and teams to deploy and manage their own Kafka brokers via kubernetes without cloud provider specific provisioning.

In classical cloud environments things like message brokers would typically be managed by a central platform team via infrastructure automation like terraform. But this means when different teams are active on such a platform there exists a bottleneck because that central platform team must handle all requests for access to the broker. With this operator teams in kubernetes gain the potential to manage the broker on their own. And because the operator integrates into the kubernetes API the teams have the same unified interface/API for all their deployments: Kubernetes YAMLs.

Additionally the operator also provides a consistent interface regardless of the environment (cloud provider, on-premise) the kubernetes cluster runs in. This means in usecases where teams have to deploy to clusters running in different environments they still get the same interface on all clusters and do not have to concern themselves with any differences.

**Note**: This operator is a work-in-progress and hasn't been tested thoroughly yet. As such it might not yet be suited for production deployments. As always, use at your own risk.

Main features:

* Provides Kubernetes Custom resources for deploying and managing Kafka brokers, topics and users
* Abstracted, unified API regardless of target environment (cloud, on-premise)

Currently supported backends:

* [Azure Event Hub](https://learn.microsoft.com/en-us/azure/event-hubs/)
* [Strimzi](https://strimzi.io) (proof-of-concept, not for production environments)

Strimzi itself is also a kubernetes operator that deploys and manages Kafka clusters inside Kubernetes. The hybrid-cloud-kafka-operator abstracts over that and provides a limited interface. If you only intend to use Kafka inside on-premise kubernetes clusters (with or without Strimzi) and have no plans to also include cloud-managed offerings then you are better of using Strimzi directly as it provides more fine-grained control. The hybrid-cloud-kafka-operator is a good choice if you plan to also use cloud-provider offerings as it then provides the needed abstraction and unification between all options.

## Quickstart

To test out the operator you just need a kubernetes cluster (you can for example create a local one with [k3d](https://k3d.io/)) and cluster-admin rights on it.

1. Run `helm repo add hybrid-cloud-kafka-operator https://maibornwolff.github.io/hybrid-cloud-kafka-operator/` to prepare the helm repository.
2. Run `helm install hybrid-cloud-kafka-operator-crds hybrid-cloud-kafka-operator/hybrid-cloud-kafka-operator-crds` and `helm install hybrid-cloud-kafka-operator hybrid-cloud-kafka-operator/hybrid-cloud-kafka-operator` to install the operator.
3. Check if the pod of the operator is running and healthy: `kubectl get pods -l app.kubernetes.io/name=hybrid-cloud-kafka-operator`.
4. Create your first broker and topic: `kubectl apply -f examples/simple.yaml`. The operator will now use strimzi to deploy a kafka broker inside the cluster.
5. Wait for the broker to be provisioned (this can take a minute or two): `kubectl wait --for=jsonpath='{.status.deployment.status}'=finished kafkabroker demo`.
6. Retrieve the credentials for the topic: `kubectl get secret demotopic-kafka-credentials -o yaml`. You can now connect to the broker (from inside the kubernetes cluster) and publish and subscribe to the topic.
7. After you are finished, delete everything: `kubectl delete -f examples/simple.yaml`.

## Operations Guide

To achieve its hybrid-cloud feature the operator abstracts between the generic API (Custom resources `KafkaBroker`, `KafkaTopic`, `KafkaUser`) and the concrete implementation for a specific cloud service. The concrete implementations are called backends. You can configure which backends should be active in the configuration. If you have several backends active the user can also select one.

The operator can be configured using a yaml-based config file. This is the complete configuration file with all options. Please refer to the comments in each line for explanations:

```yaml
handler_on_resume: false  # If set to true the operator will reconcile every available resource on restart even if there were no changes
backend: strimzi  # Default backend to use, required, allowed: azureeventhub, strimzi
allowed_backends: []  # List of backends the users can select from. If list is empty the default backend is always used regardless of if the user selects a backend 
backends:
  azureeventhub:  # Azure EventHub backend related configuration
    subscription_id: 1-2-3-4-5  # Azure Subscription id to provision eventhub in, required
    location: westeurope  # Location to provision eventhub in, required
    resource_group: foobar-rg  # Resource group to provision eventhub in, required
    broker:
      classes:  # Map of instance classes that the user can select from, optional
        small:  # Name of the class, required
          sku: Standard  # SKU to use, optional, if not set default_sku is used
          capacity: 2  # capacity, optional, if not set default_capacity is used
      default_sku: Basic  # Default SKU to use for the Eventhub namespaces. Allowed values: Basic, Standard, Premium, Default is Basic, optional
      default_capacity: 1  # Default capacity to use for the Eventhub namespaces. Range 1-20, optional
      name_pattern: "{namespace}-{name}"  # Name pattern to use for the Eventhub namespaces, optional
      fake_delete: false  # If set to true the operator will not actually delete the eventhub namespace when the object in kubernetes is deleted, optional
      network:
        public_network_access: true # Allow access to the eventhub namespace from the public internet, optional
        allow_trusted_services: true # Allow access to the eventhub namespace from trusted azure services (relevant if public access is disabled), optional
        create_private_endpoint: false # If set to true the operator will create private endpoints for each subnet listed unter allowed_subnets, optional
        allowed_ips: # List of IP nets to allow access to the Eventhub namespace, optional
          - cidr: 127.0.0.1/128 # IP net CIDR
        allowed_subnets: # List of VNet subnets to allow access to the Eventhub namespace, optional
          - vnet: # Name of the virtual network, required
            subnet: # Name of the subnet, required
            resource_group: # Name of the resource group to provision the private endpoint in, if not set group of eventhub will be used, optional
    topic:
      fake_delete: false  # If set to true the operator will not actually delete the eventhub when the object in kubernetes is deleted, optional
      name_pattern: "{namespace}-{name}"  # Name pattern to use for the Eventhub, optional
    user:
      name_pattern: "{namespace}-{name}"  # Name pattern to use for the Eventhub Authorization rule, optional
  strimzi:  # Strimzi backend related configuration
    broker:
      classes:  # Map of instance classes that the user can select from, optional
        small:  # Name of the class, required
          version: "3.2.3"  # Kafka version to use, optional
          kafka_replicas: 1  # Number of replicas for the broker, optional
          zookeeper_replicas: 3  # Number of replicas for zookeeper, optional
          kafka_storage:  # struct/map as defined in strimzi for spec.kafka.storage (https://strimzi.io/docs/operators/latest/configuring.html#type-KafkaClusterSpec-schema-reference), default is ephemeral, optional
          zookeeper_storage:  # struct/map as defined in strimzi for spec.zookeeper.storage (https://strimzi.io/docs/operators/latest/configuring.html#type-ZookeeperClusterSpec-reference), default is ephemeral, optional
          kafka_config:   # map of kafka config options to use for the broker, optional
```

For the operator to interact with Azure it needs credentials. For local testing it can pick up the token from the azure cli but for real deployments it needs a dedicated service principal. Supply the credentials for the service principal using the environment variables `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET` (if you deploy via the helm chart use the use `envSecret` value). The service principal needs permissions to manage Azure Event Hubs.

### Azure Event Hubs

If you configure the operator to create private endpoints, some rquirements must be met:

* The user/principal/identity the operator uses must have permissions to manage networks (to configure the private endpoints) and DNS zones.
* You must have at least one virtual network with a subnet in the resource group.
* The resource group must have an existing private DNS zone for the domain `privatelink.servicebus.windows.net`.
* The DNS zone must be linked to any virtual network that should access an Event Hub.

Right now the operator only supports one private endpoint per resource group due to the connection with the private DNS zone. Also if you change the list of subnets and remove one the operator will not remove the endpoints from existing Event Hub namespaces.

### Deployment

The operator can be deployed via helm chart:

1. Run `helm repo add hybrid-cloud-kafka-operator https://maibornwolff.github.io/hybrid-cloud-kafka-operator/` to prepare the helm repository.
2. Run `helm install hybrid-cloud-kafka-operator-crds hybrid-cloud-kafka-operator/hybrid-cloud-kafka-operator-crds` to install the CRDs for the operator.
3. Run `helm install hybrid-cloud-kafka-operator hybrid-cloud-kafka-operator/hybrid-cloud-kafka-operator -f values.yaml` to install the operator.

Configuration of the operator is done via helm values. For a full list of the available values see the [values.yaml in the chart](./helm/hybrid-cloud-kafka-operator/values.yaml). These are the important ones:

* `operatorConfig`: overwrite this with your specific operator config
* `envSecret`: Name of a secret with sensitive credentials (e.g. Azure service principal credentials)
* `serviceAccount.create`: Either set this to true or create the serviceaccount with appropriate permissions yourself and set `serviceAccount.name` to its name
* `strimzi.enable`: By default set to `true`. Change this to `false` if you do not want to use the strimzi backend or want to install strimzi yourself

## User Guide

The operator is completely controlled via Kubernetes custom resources (`KafkaBroker`, `KafkaTopic` and `KafkaTopicUser`). Once a broker object is created the operator will utilize one of its backends to provision an actual kafka broker. For each broker one or more topics can be created by creating `KafkaTopic` objects that reference that broker. Each topic automatically gets a user that is authorized to read and write to the topic. Additional users can be created using the `KafkaTopicUser` resource.

The `KafkaBroker` resource has the following options:

```yaml
apiVersion: hybridcloud.maibornwolff.de/v1alpha1
kind: KafkaBroker
metadata:
  name: foobroker  # Name of the broker, required
  namespace: teamfoo  # Kubernetes Namespace of the broker, required
spec:
  backend: strimzi # Name of the backend to use, optional, should be left empty unless provided by the admin
  size:  # Size configuration, optional
    class: small  # Name of a size class, available classes are specified by the operator admin. Use only if told to by your admin.
```

For each broker one or more topics can be created with the `KafkaTopic` resource which has the following options:

```yaml
apiVersion: hybridcloud.maibornwolff.de/v1alpha1
kind: KafkaTopic
metadata:
  name: sometopic  # Name of the topic, required
  namespace: teamfoo  # Namespace of the topic, required
spec:
  brokerRef:  # Reference to the broker, required
    name: foobroker  # Name of the broker, required
  kafka:  # Kafka-specific configuration, optional
    partitions: 1  # Number of partitions, optional
    retentionInDays: 1  # Retention to use for the topic in days, optional
  credentialsSecret: sometopic-topic-credentials  # Name of a secret where the credentials for the topic should be stored
```

Note that the actual name of the topic in the broker can be different from what you specify (by default a combination of name and namespace is used).

For each topic one or more users can be created with the `KafkaTopicUser` resource which has the following options:

```yaml
apiVersion: hybridcloud.maibornwolff.de/v1alpha1
kind: KafkaTopicUser
metadata:
  name: sometopic  # Name of the user, required
  namespace: teamfoo  # Namespace of the user, required
spec:
  topicRef:  # Reference to the topic, required
    name: sometopic  # Name of the topic, required
  permissions:  # Permissions for the topic, required
    consume: true  # Permission to read from the topic
    produce: true  # Permission to write to the topic
  credentialsSecret: sometopic-listener-kafka-credentials  # Name of a secret where the credentials for the user should be stored
```

A service/application that wants to use a topic should depend on the credentials secret of the topic or user and use its values for the connection. That way it is independent of the actual backend. Provided keys in the secret are:

* `bootstrap_servers`: A combination hostname:port to use for connecting to the broker
* `security_protocol`: Currently always set to `SASL_SSL`, meaning you must use TLS encryption when connecting to the broker (maps to option `security.protoco` in most clients)
* `topic`: Name of the topic
* `sasl_mechanism`: Either `PLAIN` or `SCRAM-SHA-256`
* `username`: Username to authenticate with
* `password`: Password to use for authentication
* `sasl.jaas.config`: Provided only if `sasl_mechanism==SCRAM-SHA-256`. Contains the JAAS configuration property

## Development

The operator is implemented in Python using the [Kopf](https://github.com/nolar/kopf) ([docs](https://kopf.readthedocs.io/en/stable/)) framework.

To run it locally follow these steps:

1. Create and activate a local python virtualenv
2. Install dependencies: `pip install -r requirements.txt`
3. Setup a local kubernetes cluster, e.g. with k3d: `k3d cluster create`
4. Apply the CRDs in your local cluster: `kubectl apply -f helm/hybrid-cloud-kafka-operator-crds/templates/`
5. If you want to use the Azure Event Hub backend: Either have the azure cli installed and configured with an active login or export the following environment variables: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
6. Create a `config.yaml` to suit your needs
7. Run `kopf run main.py -A`
8. In another window apply some objects to the cluster to trigger the operator (see the `examples` folder)

To deploy the operator via helm from the git repository you need to initialize the chart dependencies by running `helm dependency update helm/hybrid-cloud-kafka-operator`.

The code is structured in the following packages:

* `handlers`: Implements the operator interface for the provided custom resources, reacts to create/update/delete events in handler functions
* `backends`: Backends for the different environments
* `util`: Helper and utility functions

### Tips and tricks

* Kopf marks every object it manages with a finalizer, that means that if the operator is down or doesn't work a `kubectl delete` will hang. To work around that edit the object in question (`kubectl edit <type> <name>`) and remove the finalizer from the metadata. After that you can normally delete the object. Note that in this case the operator will not take care of cleaning up any azure resources.
* If the operator encounters an exception while processing an event in a handler, the handler will be retried after a short back-off time. During the development you can then stop the operator, make changes to the code and start the operator again. Kopf will pick up again and rerun the failed handler.
* When a handler was successfull but you still want to rerun it you need to fake a change in the object being handled. The easiest is adding or changing a label.
