import kopf
from .routing import kafka_backend
from hybridcloud_core.configuration import config_get
from hybridcloud_core.operator.reconcile_helpers import ignore_control_label_change, process_action_label
from hybridcloud_core.k8s.api import patch_namespaced_custom_object_status, create_or_update_secret, get_secret, delete_secret
from ..util import k8s
from ..util.constants import BACKOFF


if config_get("handler_on_resume", default=False):
    @kopf.on.resume(*k8s.KafkaBroker.kopf_on(), backoff=BACKOFF)
    async def broker_resume(spec, meta, labels, name, namespace, body, status, retry, diff, logger, **kwargs):
        await broker_manage(spec, meta, labels, name, namespace, body, status, retry, diff, logger, **kwargs)


@kopf.on.create(*k8s.KafkaBroker.kopf_on(), backoff=BACKOFF)
@kopf.on.update(*k8s.KafkaBroker.kopf_on(), backoff=BACKOFF)
async def broker_manage(spec, meta, labels, name, namespace, body, status, retry, diff, logger, **kwargs):
    if ignore_control_label_change(diff):
        logger.debug("Only control labels removed. Nothing to do.")
        return

    if status and "backend" in status:
        backend_name = status["backend"]
    else:
        backend_name = spec.get("backend", config_get("backend", fail_if_missing=True))
        allowed_backends = config_get("allowed_backends")
        if allowed_backends and backend_name not in allowed_backends:
            raise kopf.PermanentError(f"Backend {backend_name} is not allowed")
    backend = kafka_backend(backend_name, logger)

    valid, reason = backend.broker_spec_valid(namespace, name, spec)
    if not valid:
        _status(name, namespace, status, "failed", f"Validation failed: {reason}")
        raise kopf.PermanentError("Spec is invalid, check status for details")
    _status(name, namespace, status, "working", "Creating/updating broker", backend=backend_name)

    logger.info("Starting create/update of broker")
    # Create broker
    broker_info = await backend.create_or_update_broker(namespace, name, spec)

    if "credentialsSecret" in spec:
        logger.info("Handling broker credentials")
        credentials_secret = get_secret(namespace, spec["credentialsSecret"])
        reset_credentials = False

        def action_reset_credentials():
            nonlocal credentials_secret
            nonlocal reset_credentials
            credentials_secret = None
            reset_credentials = True
            return "Credentials reset"
        process_action_label(labels, {
            "reset-credentials": action_reset_credentials,
        }, body, k8s.KafkaBroker)

        # Generate credentials
        if not credentials_secret:
            credentials = await backend.create_or_update_broker_credentials(broker_info, reset_credentials=reset_credentials)
            create_or_update_secret(namespace, spec["credentialsSecret"], credentials)

    # mark success
    _status(name, namespace, status, "finished", "Broker created", backend=backend_name, broker_info=broker_info)


@kopf.on.delete(*k8s.KafkaBroker.kopf_on(), backoff=BACKOFF)
async def broker_delete(spec, status, name, namespace, logger, **kwargs):
    if status and "backend" in status:
        backend_name = status["backend"]
    else:
        backend_name = config_get("backend", fail_if_missing=True)
    logger.info("Starting delete of broker")
    backend = kafka_backend(backend_name, logger)
    if backend.broker_exists(namespace, name):
        await backend.delete_broker(namespace, name)
    else:
        logger.warn("Broker does not exist in backend. Nothing to do")


def _status(name, namespace, status_obj, status, reason=None, backend=None, broker_info=None):
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
    status_obj["deployment"] = {
        "status": status,
        "reason": reason
    }
    patch_namespaced_custom_object_status(k8s.KafkaBroker, namespace, name, status_obj)
