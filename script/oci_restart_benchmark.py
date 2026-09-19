"""Measure START of one previously booted E6 AX VM, then terminate it."""

import argparse
import datetime
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from types import SimpleNamespace

from oci_benchmark import (
    Clock, SshProbe, latest_image, launch, public_ip, tcp_open,
    terminate, wait_for_state,
)
from oci_common import clients, compartment_id, log, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network-file", default="network.json")
    parser.add_argument("--output", default="restart-benchmark.json")
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    c = clients()
    compute = c["compute"]
    network = read_json(args.network_file)
    domain = network["availability_domains"][0]
    shape = "VM.Standard.E6.Ax.Flex"
    image = latest_image(compute, compartment_id(), "Oracle Linux", "9", shape)
    payload = {
        "meta": {
            "region": os.environ["OCI_CLI_REGION"], "availability_domain": domain,
            "shape": shape, "ocpus": 2, "memory_gb": 8, "boot_volume_gb": 50,
            "image": image.display_name, "image_id": image.id,
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "stop_action": "SOFTSTOP", "start_action": "START",
        },
        "restarts": [],
    }
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
            clock = Clock()
            instance = launch(compute, SimpleNamespace(
                shape=shape, ocpus=2, memory_gb=8, boot_volume_gb=50, _iteration="restart",
            ), network, domain, image, Path(key + ".pub").read_text(), clock)
            instance_id = instance.id
            payload["instance_id"] = instance_id
            clock.mark("launch_api_returned")
            deadline = time.monotonic() + 900
            ip = None
            while time.monotonic() < deadline and not ip:
                ip = public_ip(compute, c["network"], compartment_id(), instance_id)
                if not ip:
                    time.sleep(1)
            if not ip:
                raise RuntimeError("public IP timeout")
            ssh = ["ssh", "-i", key, "-o", "StrictHostKeyChecking=accept-new",
                   "-o", f"UserKnownHostsFile={directory}/known_hosts",
                   "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                   "-o", "LogLevel=ERROR", f"opc@{ip}"]

            def ready(clock, probe):
                wait_for_state(compute, instance_id, "RUNNING", 900)
                clock.mark("state_running")
                probe.join(timeout=max(0, probe.deadline - time.monotonic()))
                if probe.reachable_at is None:
                    raise RuntimeError("SSH banner timeout")
                clock.marks["ssh_reachable"] = probe.reachable_at
                log(f"  ssh_reachable: +{probe.reachable_at:.3f}s")
                # Check an authenticated command, including its completion time.
                for attempt in range(30):
                    result = subprocess.run(ssh + ["cat /proc/sys/kernel/random/boot_id"],
                                            capture_output=True, text=True, timeout=15)
                    if result.returncode == 0 and result.stdout.strip():
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError("authenticated command failed")
                clock.mark("command_completed")
                row = {"marks": clock.marks, "boot_id": result.stdout.strip(),
                       "ssh_probe_window": probe.probe_window}
                # Finish first-boot initialization before stopping, outside timing.
                # A degraded systemd state is terminal too; retain diagnostics.
                settled = subprocess.run(ssh + ["sudo cloud-init status --wait; "
                                               "systemctl is-system-running --wait; "
                                               "systemd-analyze time"],
                                         capture_output=True, text=True, timeout=300)
                row["startup_report"] = settled.stdout.strip()
                log(f"  startup report: {row['startup_report']}")
                if settled.returncode != 0:
                    raise RuntimeError(f"startup did not complete: {settled.stderr}")
                return row

            probe = SshProbe(ip, clock, deadline)
            probe.start()
            payload["fresh_launch"] = ready(clock, probe)
            write_json(args.output, payload)
            previous_boot = payload["fresh_launch"]["boot_id"]
            for iteration in range(1, args.iterations + 1):
                log(f"stop/start iteration {iteration}/{args.iterations}")
                stopped_at = time.monotonic()
                compute.instance_action(instance_id, "SOFTSTOP")
                wait_for_state(compute, instance_id, "STOPPED", 900)
                stop_seconds = round(time.monotonic() - stopped_at, 3)
                if tcp_open(ip, 22):
                    raise RuntimeError("SSH still answering while STOPPED")
                clock = Clock()
                # Probe the retained IP independently, starting before START returns.
                probe = SshProbe(ip, clock, time.monotonic() + 900)
                probe.start()
                compute.instance_action(instance_id, "START")
                clock.mark("start_api_returned")
                row = ready(clock, probe)
                if row["boot_id"] == previous_boot:
                    raise RuntimeError("boot ID did not change")
                previous_boot = row["boot_id"]
                row.update(iteration=iteration, stop_seconds=stop_seconds)
                payload["restarts"].append(row)
                write_json(args.output, payload)
    finally:
        if instance_id:
            terminate(compute, instance_id)
            wait_for_state(compute, instance_id, "TERMINATED", 900)
            payload["cleanup"] = "TERMINATED; boot-volume deletion requested"
            write_json(args.output, payload)


if __name__ == "__main__":
    main()
