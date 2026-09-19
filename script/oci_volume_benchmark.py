"""Time fresh block-volume creation and hot attachment to one running VM."""

import argparse
import datetime
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import time
import uuid

import oci

from oci_benchmark import Clock, latest_image, launch, public_ip, terminate, wait_for_state
from oci_common import clients, compartment_id, config, freeform_tags, read_json, write_json


def wait_resource(get, resource_id, target):
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        resource = get(resource_id).data
        if resource.lifecycle_state == target:
            return resource
        if resource.lifecycle_state in {"FAULTY", "TERMINATED"}:
            raise RuntimeError(f"unexpected state: {resource.lifecycle_state}")
        time.sleep(0.5)
    raise TimeoutError(f"waiting for {target}: {resource_id}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--network-file", default="network.json")
    parser.add_argument("--output", default="volume-benchmark.json")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    args.shape = "VM.Standard.E6.Ax.Flex"
    args.ocpus, args.memory_gb, args.boot_volume_gb = 2, 8, 50
    args._iteration = "volume"
    c = clients()
    compute = c["compute"]
    storage = oci.core.BlockstorageClient(config(), timeout=(10, 60))
    network = read_json(args.network_file)
    domain = network["availability_domains"][0]
    image = latest_image(compute, compartment_id(), "Oracle Linux", "9", args.shape)
    payload = {"meta": {
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "region": os.environ["OCI_CLI_REGION"], "availability_domain": domain,
        "shape": args.shape, "ocpus": 2, "memory_gb": 8,
        "image": image.display_name, "size_gib": 50, "vpus_per_gb": 10,
        "attachment_type": "paravirtualized", "poll_sleep_seconds": 0.5,
        "guest_check": "read 4096 bytes with direct I/O after ATTACHED; includes SSH overhead",
    }, "trials": []}
    instance_id = None

    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        with tempfile.TemporaryDirectory() as directory:
            key = str(Path(directory) / "key")
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key],
                           check=True, capture_output=True)
            instance = launch(compute, args, network, domain, image,
                              Path(key + ".pub").read_text(), Clock())
            instance_id = instance.id
            payload["instance_id"] = instance_id
            write_json(args.output, payload)
            wait_for_state(compute, instance_id, "RUNNING", 900)
            deadline = time.monotonic() + 600
            ip = None
            while time.monotonic() < deadline and not ip:
                ip = public_ip(compute, c["network"], compartment_id(), instance_id)
                if not ip:
                    time.sleep(1)
            if not ip:
                raise TimeoutError("public IP")
            ssh = ["ssh", "-i", key, "-o", "StrictHostKeyChecking=accept-new",
                   "-o", f"UserKnownHostsFile={directory}/known_hosts", "-o", "BatchMode=yes",
                   "-o", "ConnectTimeout=5", "-o", "LogLevel=ERROR", f"opc@{ip}"]
            while time.monotonic() < deadline:
                result = subprocess.run(ssh + ["true"], capture_output=True, timeout=15)
                if result.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise TimeoutError("SSH authentication")
            subprocess.run(ssh + ["sudo cloud-init status --wait"], check=True, timeout=300)
            for iteration in range(1, args.iterations + 1):
                volume_id = attachment_id = None
                row = {"iteration": iteration}
                payload["trials"].append(row)
                try:
                    devices = compute.list_instance_devices(instance_id, is_available=True).data
                    if not devices:
                        raise RuntimeError("no available attachment devices")
                    device_name = devices[0].name
                    details = oci.core.models.CreateVolumeDetails(
                        compartment_id=compartment_id(), availability_domain=domain,
                        display_name="rwx-volume-" + uuid.uuid4().hex,
                        size_in_gbs=50, vpus_per_gb=10, freeform_tags=freeform_tags())
                    clock = Clock()
                    volume = storage.create_volume(details, opc_retry_token=uuid.uuid4().hex).data
                    volume_id = volume.id
                    row.update(volume_id=volume_id, marks=clock.marks)
                    clock.mark("create_api_returned")
                    write_json(args.output, payload)
                    wait_resource(storage.get_volume, volume_id, "AVAILABLE")
                    clock.mark("volume_available")
                    details = oci.core.models.AttachParavirtualizedVolumeDetails(
                        instance_id=instance_id, volume_id=volume_id, is_read_only=False,
                        device=device_name)
                    clock.mark("attach_started")
                    attachment = compute.attach_volume(details).data
                    attachment_id = attachment.id
                    row["attachment_id"] = attachment_id
                    clock.mark("attach_api_returned")
                    write_json(args.output, payload)
                    attachment = wait_resource(compute.get_volume_attachment, attachment_id, "ATTACHED")
                    clock.mark("volume_attached")
                    if not attachment.device:
                        raise RuntimeError("attachment has no device path")
                    row["device"] = attachment.device
                    device = shlex.quote(attachment.device)
                    # Read only this new attachment, never enumerate or write disks.
                    command = (f"for i in $(seq 1 120); do if test -b {device}; then "
                               f"sudo dd if={device} of=/dev/null bs=4096 count=1 "
                               "iflag=direct status=none && exit 0; fi; sleep 0.25; done; exit 1")
                    subprocess.run(ssh + [command], check=True, timeout=60)
                    clock.mark("guest_read_completed")
                    row["provision_seconds"] = clock.marks["volume_available"]
                    row["attach_seconds"] = round(clock.marks["volume_attached"] - clock.marks["attach_started"], 3)
                    row["total_seconds"] = clock.marks["guest_read_completed"]
                finally:
                    if attachment_id:
                        compute.detach_volume(attachment_id)
                        wait_resource(compute.get_volume_attachment, attachment_id, "DETACHED")
                        row["attachment_cleanup"] = "DETACHED"
                    if volume_id:
                        storage.delete_volume(volume_id)
                        wait_resource(storage.get_volume, volume_id, "TERMINATED")
                        row["volume_cleanup"] = "TERMINATED"
                    write_json(args.output, payload)
    finally:
        if instance_id:
            terminate(compute, instance_id)
            wait_for_state(compute, instance_id, "TERMINATED", 900)
            payload["instance_cleanup"] = "TERMINATED; boot-volume deletion requested"
        write_json(args.output, payload)


if __name__ == "__main__":
    main()
