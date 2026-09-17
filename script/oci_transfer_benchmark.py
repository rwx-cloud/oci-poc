"""Launch a temporary E5 VM and measure same-region Object Storage throughput.

Run from an RWX sandbox. Only a short-lived bucket-scoped preauthenticated URL,
not tenancy credentials, is sent to the VM. All created resources are disposable.
"""

import argparse
import base64
import datetime
import json
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
from oci_common import clients, compartment_id, config, freeform_tags, log, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocpus", type=float, default=2)
    parser.add_argument("--memory-gb", type=float, default=8)
    parser.add_argument("--size-mib", type=int, default=1024)
    parser.add_argument("--files", type=int, default=8)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 8])
    parser.add_argument("--network-file", default="network.json")
    parser.add_argument("--output", default="transfer-benchmark.json")
    args = parser.parse_args()
    if min(args.size_mib, args.files, args.repetitions, *args.concurrency) <= 0:
        parser.error("size, files, repetitions, and concurrency must be positive")
    args.shape = "VM.Standard.E5.Flex"
    args.boot_volume_gb = 50
    args._iteration = "transfer"
    network_info = read_json(args.network_file)
    c = clients()
    cfg = config()
    storage = oci.object_storage.ObjectStorageClient(cfg)
    namespace = storage.get_namespace().data
    bucket = "rwx-transfer-" + uuid.uuid4().hex
    objects = [f"file-{i}" for i in range(args.files)]
    instance_id = None
    bucket_created = False
    par_id = None

    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        storage.create_bucket(namespace, oci.object_storage.models.CreateBucketDetails(
            name=bucket, compartment_id=compartment_id(),
            public_access_type="NoPublicAccess", storage_tier="Standard",
            freeform_tags=freeform_tags(),
        ))
        bucket_created = True
        par = storage.create_preauthenticated_request(
            namespace, bucket, oci.object_storage.models.CreatePreauthenticatedRequestDetails(
                name="temporary-transfer-benchmark", access_type="AnyObjectReadWrite",
                time_expires=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2),
            ),
        ).data
        par_id = par.id
        urls = [storage.base_client.endpoint + par.access_uri + name for name in objects]
        with tempfile.TemporaryDirectory() as directory:
            key = os.path.join(directory, "ssh")
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key],
                           check=True, capture_output=True)
            image = latest_image(c["compute"], compartment_id(), "Oracle Linux", "9", args.shape)
            instance = launch(c["compute"], args, network_info,
                              network_info["availability_domains"][0], image,
                              Path(key + ".pub").read_text(), Clock())
            instance_id = instance.id
            log(f"created temporary {args.shape} instance {instance_id}")
            wait_for_state(c["compute"], instance_id, "RUNNING", 900)
            deadline = time.monotonic() + 600
            ip = None
            while time.monotonic() < deadline and not ip:
                ip = public_ip(c["compute"], c["network"], compartment_id(), instance_id)
                if not ip:
                    time.sleep(2)
            if not ip:
                raise RuntimeError("timed out waiting for public IP")
            ssh = ["ssh", "-i", key, "-o", "StrictHostKeyChecking=accept-new",
                   "-o", f"UserKnownHostsFile={directory}/known_hosts", "-o", "BatchMode=yes",
                   "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR", f"opc@{ip}"]
            while time.monotonic() < deadline:
                ready = subprocess.run(ssh + ["true"], capture_output=True, timeout=20)
                if ready.returncode == 0:
                    break
                time.sleep(3)
            else:
                raise RuntimeError("timed out waiting for SSH authentication")
            instance = c["compute"].get_instance(instance_id).data
            meta = {
                "region": cfg["region"], "shape": args.shape, "ocpus": args.ocpus,
                "memory_gb": args.memory_gb,
                "networking_bandwidth_gbps": instance.shape_config.networking_bandwidth_in_gbps,
                "image": image.display_name, "files": args.files,
                "bytes_per_file": args.size_mib * 2**20, "repetitions": args.repetitions,
                "path": "same-region public HTTPS endpoint via internet gateway",
                "source": "RAM-backed random data; same payload, distinct objects",
                "destination": "/dev/null (no disk writes)",
                "date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            log(json.dumps(meta))
            code = Path(__file__).with_name("oci_transfer_guest.py").read_text()
            payload = json.dumps({"urls": urls, "size": args.size_mib * 2**20,
                                  "repetitions": args.repetitions, "concurrency": args.concurrency})
            # Streaming stdout contains only measurements, never credentials or URLs.
            with subprocess.Popen(ssh + ["python3 -c " + shlex.quote(code)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as process:
                process.stdin.write(payload)
                process.stdin.close()
                final = None
                for line in process.stdout:
                    row = json.loads(line)
                    if "results" in row:
                        final = row
                    else:
                        log(f"{row['direction']} c={row['concurrency']} "
                            f"rep={row['repetition']}: {row['gbps']:.3f} Gbps "
                            f"({row['mib_per_second']:.1f} MiB/s), {row['seconds']:.2f}s")
                if process.wait() != 0 or final is None:
                    raise RuntimeError("guest transfer benchmark failed")
            # Independent service-side checks, outside the timed transfer window.
            for name in objects:
                headers = storage.head_object(namespace, bucket, name).headers
                if int(headers["content-length"]) != meta["bytes_per_file"]:
                    raise RuntimeError("stored object size mismatch")
                expected_md5 = base64.b64encode(bytes.fromhex(final["source_md5"])).decode()
                if headers["content-md5"] != expected_md5:
                    raise RuntimeError("stored object checksum mismatch")
            write_json(args.output, {"meta": meta, "validation": "all object sizes and Content-MD5 checksums match",
                                     "results": final["results"]})
    finally:
        # Attempt storage cleanup even if compute cleanup fails.
        try:
            if instance_id:
                terminate(c["compute"], instance_id)
                wait_for_state(c["compute"], instance_id, "TERMINATED", 900)
        finally:
            if bucket_created:
                if par_id:
                    storage.delete_preauthenticated_request(namespace, bucket, par_id)
                for name in objects:
                    try:
                        storage.delete_object(namespace, bucket, name)
                    except oci.exceptions.ServiceError as error:
                        if error.status != 404:
                            raise
                storage.delete_bucket(namespace, bucket)
                log("temporary bucket and objects deleted")


if __name__ == "__main__":
    main()
