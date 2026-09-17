"""Benchmark how long OCI takes to boot and terminate a VM.

One iteration measures, from a single wall clock:

  launch_api_returned  LaunchInstance accepted the request
  public_ip_assigned   the VNIC has a public address
  state_running        the control plane reports lifecycle_state RUNNING
  ssh_reachable        sshd answered on port 22, i.e. the guest is usable
  terminate_api        TerminateInstance accepted the request
  state_terminated     the control plane reports lifecycle_state TERMINATED

Instances are always terminated, including on failure or SIGTERM, and they carry
freeform tags so script/oci_sweep.py can clean up anything that leaks.
"""

import argparse
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import oci

from oci_common import (
    clients,
    compartment_id,
    die,
    freeform_tags,
    log,
    read_json,
    write_json,
)

SSH_PORT = 22
STATE_POLL_SECONDS = 1.0
# A dropped SYN costs the full connect timeout, so this dominates the SSH
# measurement's resolution far more than the sleep between probes does.
TCP_CONNECT_TIMEOUT = 1.0
TCP_POLL_SECONDS = 0.25


class Clock:
    """Records named marks relative to a single t0."""

    def __init__(self):
        self.t0 = time.monotonic()
        self.marks = {}

    def reset(self):
        """Restart t0. Used so a throttled-and-retried launch is timed from the
        attempt that actually succeeded, not from the first one that got a 429."""
        self.t0 = time.monotonic()

    def mark(self, name):
        self.marks[name] = round(time.monotonic() - self.t0, 3)
        log(f"  {name}: +{self.marks[name]:.3f}s")
        return self.marks[name]


def latest_image(compute, compartment, operating_system, os_version, shape):
    images = compute.list_images(
        compartment,
        operating_system=operating_system,
        operating_system_version=os_version,
        shape=shape,
        sort_by="TIMECREATED",
        sort_order="DESC",
        lifecycle_state="AVAILABLE",
    ).data
    if not images:
        die(
            f"no {operating_system} {os_version} image available for shape {shape}"
        )
    return images[0]


def with_retry(call, attempts=6, base_delay=5.0):
    """Retry a control-plane call through OCI's per-user rate limiting.

    LaunchInstance is throttled per user, so concurrent benchmark runs (or a
    tenancy busy with other work) get a 429 rather than a queued request.
    """
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except oci.exceptions.ServiceError as error:
            retryable = error.status == 429 or error.status >= 500
            if not retryable or attempt == attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log(f"  {error.status} {error.code}, retrying in {delay:.0f}s")
            time.sleep(delay)


def launch(compute, args, network_info, availability_domain, image, public_key, clock):
    details = oci.core.models.LaunchInstanceDetails(
        availability_domain=availability_domain,
        compartment_id=network_info["compartment_id"],
        display_name=f"rwx-oci-poc-{int(time.time())}",
        shape=args.shape,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=float(args.ocpus), memory_in_gbs=float(args.memory_gb)
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=image.id, boot_volume_size_in_gbs=args.boot_volume_gb
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=network_info["subnet_id"], assign_public_ip=True
        ),
        metadata={"ssh_authorized_keys": public_key},
        # The Oracle Cloud Agent plugins add work to first boot that we are not
        # measuring; leaving them at their defaults keeps this comparable to a
        # stock instance launch.
        freeform_tags=freeform_tags({"iteration": str(args._iteration)}),
    )
    def attempt():
        clock.reset()
        return compute.launch_instance(details)

    return with_retry(attempt).data


def public_ip(compute, network, compartment, instance_id):
    attachments = compute.list_vnic_attachments(
        compartment, instance_id=instance_id
    ).data
    for attachment in attachments:
        if attachment.lifecycle_state != "ATTACHED":
            continue
        vnic = network.get_vnic(attachment.vnic_id).data
        if vnic.public_ip:
            return vnic.public_ip
    return None


def tcp_open(host, port, timeout=TCP_CONNECT_TIMEOUT):
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            # Wait for the SSH identification string rather than treating a bare
            # accepted connection as ready; OCI's edge can accept before sshd is.
            banner = sock.recv(64)
            return banner.startswith(b"SSH-")
    except OSError:
        return False


class SshProbe(threading.Thread):
    """Probes port 22 from the moment the IP exists, independent of state polling.

    Records the window between the start of the last failed probe and the start
    of the successful one. sshd became reachable somewhere inside that window, so
    it is the upper bound on how much of the reported time is our own polling
    latency rather than OCI's.
    """

    def __init__(self, ip, clock, deadline):
        super().__init__(daemon=True)
        self.ip = ip
        self.clock = clock
        self.deadline = deadline
        self.reachable_at = None
        self.probe_window = None
        self.probes = 0

    def run(self):
        last_failed_start = None
        while time.monotonic() < self.deadline:
            started = time.monotonic()
            if tcp_open(self.ip, SSH_PORT):
                self.reachable_at = round(started - self.clock.t0, 3)
                if last_failed_start is not None:
                    self.probe_window = round(started - last_failed_start, 3)
                return
            last_failed_start = started
            self.probes += 1
            time.sleep(TCP_POLL_SECONDS)


def guest_boot_report(host, user, key_path):
    """Best effort in-guest timings. Never fails the benchmark."""
    command = [
        "ssh",
        "-i", key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR",
        "-o", "BatchMode=yes",
        f"{user}@{host}",
        # uptime tells us how long the guest kernel had been running when sshd
        # first answered; systemd-analyze breaks that down once boot completes.
        "awk '{print \"guest_uptime_s=\" $1}' /proc/uptime; "
        "systemd-analyze time 2>&1 | head -2",
    ]
    for attempt in range(6):
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return result.stdout.strip()
        time.sleep(2 * (attempt + 1))
    return f"unavailable: {result.stderr.strip()}"


def terminate(compute, instance_id):
    try:
        compute.terminate_instance(instance_id, preserve_boot_volume=False)
    except oci.exceptions.ServiceError as error:
        if error.status != 404:
            raise


def wait_for_state(compute, instance_id, target, timeout):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        state = compute.get_instance(instance_id).data.lifecycle_state
        if state != last:
            log(f"  lifecycle_state={state}")
            last = state
        if state == target:
            return state
        if target == "RUNNING" and state in ("TERMINATING", "TERMINATED"):
            die(f"instance reached {state} while waiting for RUNNING")
        time.sleep(STATE_POLL_SECONDS)
    die(f"timed out after {timeout}s waiting for {target}")


def run_iteration(args, network_info, c, image, availability_domain, public_key):
    compute, network = c["compute"], c["network"]
    compartment = network_info["compartment_id"]
    clock = Clock()
    instance_id = None
    result = {"iteration": args._iteration, "availability_domain": availability_domain}

    try:
        instance = launch(
            compute, args, network_info, availability_domain, image, public_key, clock
        )
        instance_id = instance.id
        result["instance_id"] = instance_id
        args._live.add(instance_id)
        clock.mark("launch_api_returned")

        ip = None
        probe = None
        deadline = time.monotonic() + args.boot_timeout
        state_seen = False
        while time.monotonic() < deadline:
            if not state_seen:
                state = compute.get_instance(instance_id).data.lifecycle_state
                if state == "RUNNING":
                    clock.mark("state_running")
                    state_seen = True
                elif state in ("TERMINATING", "TERMINATED"):
                    die(f"instance reached {state} while waiting for RUNNING")
            if ip is None:
                ip = public_ip(compute, network, compartment, instance_id)
                if ip:
                    result["public_ip"] = ip
                    clock.mark("public_ip_assigned")
                    # Start probing immediately. Waiting for RUNNING first would
                    # let sshd come up inside a window we were not watching.
                    probe = SshProbe(ip, clock, deadline)
                    probe.start()
            if state_seen and ip:
                break
            time.sleep(STATE_POLL_SECONDS)
        else:
            die(f"timed out after {args.boot_timeout}s waiting for RUNNING + public IP")

        probe.join(timeout=max(0.0, deadline - time.monotonic()))
        if probe.reachable_at is None:
            die(f"timed out after {args.boot_timeout}s waiting for ssh on {ip}")
        clock.marks["ssh_reachable"] = probe.reachable_at
        log(f"  ssh_reachable: +{probe.reachable_at:.3f}s")
        result["ssh_probe_window"] = probe.probe_window
        result["ssh_probes"] = probe.probes
        log(
            f"  ssh measurement uncertainty: <={probe.probe_window}s "
            f"over {probe.probes} failed probe(s)"
        )

        if args.guest_report:
            result["guest"] = guest_boot_report(ip, args.ssh_user, args.ssh_key)
            for line in result["guest"].splitlines():
                log(f"  guest: {line}")

        terminate_started = time.monotonic()
        terminate(compute, instance_id)
        clock.mark("terminate_api")
        wait_for_state(compute, instance_id, "TERMINATED", args.terminate_timeout)
        clock.mark("state_terminated")
        args._live.discard(instance_id)
        result["terminate_seconds"] = round(
            clock.marks["state_terminated"] - (terminate_started - clock.t0), 3
        )
    finally:
        if instance_id and instance_id in args._live:
            log(f"  cleaning up {instance_id}")
            terminate(compute, instance_id)
            args._live.discard(instance_id)

    result["marks"] = clock.marks
    return result


def summarize(results):
    def series(key):
        return sorted(
            r["marks"][key] for r in results if key in r.get("marks", {})
        )

    summary = {}
    for key in (
        "launch_api_returned",
        "public_ip_assigned",
        "state_running",
        "ssh_reachable",
        "terminate_api",
        "state_terminated",
    ):
        values = series(key)
        if values:
            summary[key] = {
                "min": values[0],
                "median": round(statistics.median(values), 3),
                "max": values[-1],
                "samples": len(values),
            }

    windows = sorted(
        r["ssh_probe_window"]
        for r in results
        if r.get("ssh_probe_window") is not None
    )
    if windows:
        summary["ssh_probe_window"] = {
            "min": windows[0],
            "median": round(statistics.median(windows), 3),
            "max": windows[-1],
            "samples": len(windows),
        }

    terminates = sorted(
        r["terminate_seconds"] for r in results if "terminate_seconds" in r
    )
    if terminates:
        summary["terminate_duration"] = {
            "min": terminates[0],
            "median": round(statistics.median(terminates), 3),
            "max": terminates[-1],
            "samples": len(terminates),
        }
    return summary


def render(summary, meta):
    lines = []
    lines.append("")
    lines.append("=" * 78)
    lines.append("OCI VM boot + terminate benchmark")
    lines.append("=" * 78)
    for key, value in meta.items():
        lines.append(f"{key:<22} {value}")
    lines.append("")
    lines.append(
        f"{'phase (from launch request)':<32}{'min':>10}{'median':>10}{'max':>10}"
    )
    lines.append("-" * 78)
    labels = {
        "launch_api_returned": "LaunchInstance returned",
        "public_ip_assigned": "public IP assigned",
        "state_running": "lifecycle RUNNING",
        "ssh_reachable": "sshd answering (usable)",
        "terminate_api": "TerminateInstance returned",
        "state_terminated": "lifecycle TERMINATED",
    }
    for key, label in labels.items():
        row = summary.get(key)
        if not row:
            continue
        lines.append(
            f"{label:<32}{row['min']:>9.1f}s{row['median']:>9.1f}s{row['max']:>9.1f}s"
        )
    lines.append("-" * 78)
    row = summary.get("terminate_duration")
    if row:
        lines.append(
            f"{'terminate wall time':<32}{row['min']:>9.1f}s"
            f"{row['median']:>9.1f}s{row['max']:>9.1f}s"
        )
    lines.append("=" * 78)
    window = summary.get("ssh_probe_window")
    if window:
        lines.append(
            f"ssh timing is polled, so it is overstated by at most "
            f"{window['max']:.2f}s (worst case across iterations)."
        )
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shape", default="VM.Standard.E5.Flex")
    parser.add_argument("--ocpus", type=float, default=2)
    parser.add_argument("--memory-gb", type=float, default=8)
    parser.add_argument("--boot-volume-gb", type=int, default=50)
    parser.add_argument("--operating-system", default="Oracle Linux")
    parser.add_argument("--operating-system-version", default="9")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--boot-timeout", type=int, default=900)
    parser.add_argument("--terminate-timeout", type=int, default=900)
    parser.add_argument("--ssh-user", default="opc")
    parser.add_argument("--ssh-key", default=os.path.expanduser("~/.ssh/rwx_oci_poc"))
    parser.add_argument("--guest-report", action="store_true")
    parser.add_argument("--network-file", default="network.json")
    parser.add_argument("--output", default="benchmark.json")
    args = parser.parse_args()

    args._live = set()
    args._iteration = 0

    network_info = read_json(args.network_file)
    c = clients()

    with open(args.ssh_key + ".pub") as handle:
        public_key = handle.read().strip()

    image = latest_image(
        c["compute"],
        network_info["compartment_id"],
        args.operating_system,
        args.operating_system_version,
        args.shape,
    )
    log(f"image: {image.display_name} ({image.id})")

    def on_signal(signum, _frame):
        log(f"received signal {signum}, terminating {len(args._live)} instance(s)")
        for instance_id in list(args._live):
            terminate(c["compute"], instance_id)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    domains = network_info["availability_domains"]
    results = []
    try:
        for i in range(1, args.iterations + 1):
            args._iteration = i
            availability_domain = domains[(i - 1) % len(domains)]
            log(f"iteration {i}/{args.iterations} in {availability_domain}")
            results.append(
                run_iteration(
                    args, network_info, c, image, availability_domain, public_key
                )
            )
    finally:
        for instance_id in list(args._live):
            log(f"terminating leaked instance {instance_id}")
            terminate(c["compute"], instance_id)

    summary = summarize(results)
    meta = {
        "region": os.environ["OCI_CLI_REGION"],
        "shape": args.shape,
        "ocpus": f"{args.ocpus:g} OCPU ({args.ocpus * 2:g} vCPU on x86 shapes)",
        "memory": f"{args.memory_gb:g} GB",
        "boot volume": f"{args.boot_volume_gb} GB",
        "image": image.display_name,
        "iterations": args.iterations,
    }
    write_json(args.output, {"meta": meta, "summary": summary, "results": results})
    print(render(summary, meta), flush=True)


if __name__ == "__main__":
    main()
