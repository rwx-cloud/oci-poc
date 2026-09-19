# E6 AX start from stopped

**Starting a previously booted, stopped E6 AX VM reached SSH in a median
17.115 seconds; an authenticated command completed at 19.644 seconds.**
This is substantially faster than the earlier fresh-launch median of 42.605 s,
although this experiment repeats one VM rather than sampling three new VMs.

## Environment

| Setting | Value |
| --- | --- |
| Shape | `VM.Standard.E6.Ax.Flex` |
| CPU / memory | 2 OCPUs / 4 vCPUs / 8 GB RAM |
| Boot volume | 50 GB |
| Region / domain | `us-ashburn-1` / `nivC:US-ASHBURN-AD-1` |
| Image | `Oracle-Linux-9.8-2026.08.14-0` |
| Recorded start | `2026-09-19T01:38:55.726718+00:00` (benchmark clock) |
| Lifecycle | One fresh launch, then three `SOFTSTOP` → `STOPPED` → `START` cycles |

## Results

Fresh-launch offsets begin at `LaunchInstance`; restart offsets begin immediately
before the SSH probe is started and the `START` API is called. Stop duration is
separate and excluded from the restart times.

| Trial | OCI `RUNNING` | SSH answering | Authenticated command completed |
| --- | ---: | ---: | ---: |
| This VM's fresh launch | 38.563 s | 53.520 s | 54.874 s |
| Restart 1 | 4.108 s | 16.850 s | 19.175 s |
| Restart 2 | 4.955 s | 17.389 s | 19.959 s |
| Restart 3 | 5.148 s | 17.115 s | 19.644 s |
| **Restart median** | **4.955 s** | **17.115 s** | **19.644 s** |

The restart SSH median was 36.405 s (68.0%) below this VM's fresh launch.
That fresh launch was slower than the earlier
[three-instance E6 AX benchmark](E6-AX-BOOT-BENCHMARK.md): relative to its
42.605 s median, restart was 25.490 s (59.8%) faster. The latter comparison
is historical, not an interleaved control.

Graceful stopping took 38.503, 39.168, and 36.142 s. These results apply when
a VM is **already stopped** when work arrives; they do not imply that stopping
and starting an active VM takes only 17 seconds.

## Methodology and validation

- Ran `script/oci_restart_benchmark.py` through `rwx sandbox exec`.
- Waited for cloud-init and systemd startup to finish before stopping.
- Confirmed OCI `STOPPED` and that SSH no longer answered before every start.
- Probed the retained public IP independently of control-plane polling, using
  the existing SSH-banner probe. Largest recorded probe window: 0.273 s.
- Read `/proc/sys/kernel/random/boot_id` over authenticated SSH after every
  start; all four boot IDs were different. Command timings include the SSH
  connection and command round trip and occur after both `RUNNING` and banner
  checks, so they are not the earliest possible authenticated-ready timestamp.
- Guest reports showed cloud-init `done` and systemd `degraded` on the fresh
  boot and every restart. Completed systemd startup was 28.096 s initially,
  then 9.303, 9.363, and 9.144 s. The failed unit was not identified; successful
  SSH does not certify overall guest health or application readiness.
- The instance reached `TERMINATED`, with boot-volume deletion requested.
  A final run-scoped sweep found zero leftovers. An earlier diagnostic attempt
  aborted on the degraded-state exit status and was also confirmed terminated;
  it contributed no restart samples.
- Raw results are retained in the thread artifact `e6-ax-restart.json`.

## Scope and limitations

This tests three immediate restarts of one initialized VM, not three independently
allocated machines or VMs left stopped for hours. It retains the instance,
network identity, boot volume, and guest state. It is not a clean-machine reset
and does not prove which portion of provisioning or first-boot work was avoided.
No OS or security settings were changed. A stopped pool still needs storage
and state hygiene; these measurements do not establish future capacity or
long-idle startup guarantees.

## Reproduce

With the OCI vault configured for Ashburn, run in RWX:

```sh
rwx sandbox exec -- bash -lc 'set -e
export RWX_RUN_ID=e6-ax-restart-$(date +%s)
trap "python script/oci_sweep.py" EXIT
python script/oci_network.py
python script/oci_restart_benchmark.py --iterations 3'
```

The script uses the first availability domain in `network.json`; restrict that
list to AD-1 to reproduce this placement. It writes `restart-benchmark.json`,
including partial results on ordinary failures, and terminates its VM in cleanup.
A hard-killed process still needs the run-scoped sweep. The existing reusable
benchmark network is retained.
