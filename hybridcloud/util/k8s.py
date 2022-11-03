import kubernetes
from hybridcloud_core.k8s.resources import Resource, Scope
from hybridcloud_core.k8s.api import get_namespaced_custom_object, patch_namespaced_custom_object


API_GROUP = "hybridcloud.maibornwolff.de"
KafkaBroker = Resource(API_GROUP, "v1alpha1", "kafkabrokers", "KafkaBroker", Scope.NAMESPACED)
KafkaTopic = Resource(API_GROUP, "v1alpha1", "kafkatopics", "KafkaTopic", Scope.NAMESPACED)
KafkaTopicUser = Resource(API_GROUP, "v1alpha1", "kafkatopicusers", "KafkaTopicUser", Scope.NAMESPACED)


STRIMZI_API_GROUP = "kafka.strimzi.io"
StrimziKafka = Resource(STRIMZI_API_GROUP, "v1beta2", "kafkas", "Kafka", Scope.NAMESPACED)
StrimziKafkaTopic = Resource(STRIMZI_API_GROUP, "v1beta2", "kafkatopics", "KafkaTopic", Scope.NAMESPACED)
StrimziKafkaUser = Resource(STRIMZI_API_GROUP, "v1beta2", "kafkausers", "KafkaUser", Scope.NAMESPACED)
