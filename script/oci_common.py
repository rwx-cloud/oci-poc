"""Shared OCI client construction and helpers for the RWX benchmark POC."""

import json
import os
import sys
import time

import oci

POC_TAG = "rwx-oci-poc"


def config():
    cfg = {
        "user": os.environ["OCI_CLI_USER"],
        "tenancy": os.environ["OCI_CLI_TENANCY"],
        "fingerprint": os.environ["OCI_CLI_FINGERPRINT"],
        "region": os.environ["OCI_CLI_REGION"],
        "key_file": os.environ["OCI_CLI_KEY_FILE"],
    }
    oci.config.validate_config(cfg)
    return cfg


def compartment_id():
    # The tenancy OCID is also the OCID of the root compartment.
    return os.environ.get("OCI_COMPARTMENT_ID") or os.environ["OCI_CLI_TENANCY"]


def clients():
    cfg = config()
    # Short timeouts so a stalled control-plane call surfaces instead of hanging
    # the benchmark; retries are handled by the SDK's default strategy.
    return {
        "compute": oci.core.ComputeClient(cfg, timeout=(10, 60)),
        "network": oci.core.VirtualNetworkClient(cfg, timeout=(10, 60)),
        "identity": oci.identity.IdentityClient(cfg, timeout=(10, 60)),
    }


def freeform_tags(extra=None):
    tags = {
        POC_TAG: "true",
        "rwx-run-id": os.environ.get("RWX_RUN_ID", "local"),
    }
    if extra:
        tags.update(extra)
    return tags


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def write_json(path, payload):
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    log(f"wrote {path}")


def read_json(path):
    with open(path) as handle:
        return json.load(handle)


def die(message):
    print(f"error: {message}", file=sys.stderr, flush=True)
    sys.exit(1)
