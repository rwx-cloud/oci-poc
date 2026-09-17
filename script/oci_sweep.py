"""Terminate any POC instance left behind by a failed or cancelled run.

The benchmark cleans up after itself, but a hard task abort can still leak an
instance. This sweeps everything tagged by the POC, optionally scoped to the
current run.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oci

from oci_common import POC_TAG, clients, compartment_id, log


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all-runs",
        action="store_true",
        help="sweep POC instances from every run, not just this one",
    )
    args = parser.parse_args()

    compartment = compartment_id()
    compute = clients()["compute"]
    run_id = os.environ.get("RWX_RUN_ID", "local")

    instances = oci.pagination.list_call_get_all_results(
        compute.list_instances, compartment
    ).data

    swept = 0
    for instance in instances:
        if instance.lifecycle_state in ("TERMINATED", "TERMINATING"):
            continue
        tags = instance.freeform_tags or {}
        if tags.get(POC_TAG) != "true":
            continue
        if not args.all_runs and tags.get("rwx-run-id") != run_id:
            continue
        log(f"terminating {instance.display_name} ({instance.id})")
        compute.terminate_instance(instance.id, preserve_boot_volume=False)
        swept += 1

    log(f"swept {swept} instance(s)")


if __name__ == "__main__":
    main()
