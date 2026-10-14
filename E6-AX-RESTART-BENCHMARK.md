# E6 AX start from stopped

## October 14, 2026: second boot after 30 minutes stopped

**Kernel start remained approximately 10 seconds: 9.033–9.315 s after
`START`, with SSH answering at 16.464 s.** This is one completed trial,
consistent with the five immediate second boots below, not a tail-latency claim.

The VM used the same E6 AX configuration: 2 OCPUs, 8 GB RAM, 50 GB boot volume,
Ashburn AD-1, `Oracle-Linux-9.8-2026.09.18-0`. After its first boot and completed
cloud-init/systemd startup, OCI reported `STOPPED` at
**2026-10-14 16:33:07.029506 UTC**. The start timer began at
**17:03:07.584623 UTC**, after **1,800.555 seconds** stopped. Monotonic elapsed
time and independent UTC timestamp subtraction agreed. Read-only checks during
the interval and immediately before `START` confirmed the VM remained stopped.

| Phase | Fresh launch | Start after 30 minutes stopped |
| --- | ---: | ---: |
| API returned | 1.172 s | 0.494 s |
| Observed RUNNING | 27.810 s | 4.224 s |
| Inferred kernel boot-clock origin | 32.100–32.387 s | 9.033–9.315 s |
| SSH banner | 42.313 s | 16.464 s |

Offsets begin at the respective launch/start request. The kernel logged
`Run /init as init process` at 0.913 s on second boot, placing the handoff
approximately 9.95–10.23 s after `START`. This is the stock initramfs, not a
custom minimal init. Kernel-origin intervals use the same five bracketed uptime
reads as the earlier measurements; SSH is only the later retrieval channel.

The test ran in separate short `rwx sandbox exec` commands for preparation,
waiting, and restart, using a temporary driver around the existing benchmark
helpers. Two earlier single-command attempts ended at RWX's observed 30-minute
cutoff before completing the stopped interval, including a retry with one-hour
sandbox timeout settings. Neither produced a restart sample; both were excluded
and their instances terminated. Short commands during the successful wait kept
the sandbox session alive without changing the OCI VM.

The completed trial's boot ID changed, cloud-init reported `done`, and systemd
reported completed startup but `degraded`, as before. The measured VM and both
interrupted-attempt VMs were independently confirmed `TERMINATED`, with
boot-volume deletion requested. The reusable network remains.

Measured instance:
`ocid1.instance.oc1.iad.anuwcljsurwx6aqcnpqjm6bvvvtlpcp65jcvqquxdy6crl4k2xjprykebdha`.
Raw samples, timestamps, boot IDs, and kernel logs are retained as
`second-boot-after-30m.json` in the
[measurement thread](https://ampcode.com/threads/T-01a13742-e07b-7161-b8ef-73cdf253b968).

## October 13, 2026: five independent second boots

**Second-boot kernel start had a median interval of 9.666–10.011 seconds
from `START`; SSH answered at a median 16.860 seconds.** Each trial used a
different newly allocated VM, fully booted once, gracefully stopped, then
started exactly once. These are five second boots, not five cycles on one VM.

All five used `VM.Standard.E6.Ax.Flex`, 2 OCPUs / 8 GB RAM, 50 GB boot volumes,
Ashburn AD-1, and `Oracle-Linux-9.8-2026.09.18-0`. They ran sequentially via
`rwx sandbox exec` from 23:27 to 23:46 UTC. Kernel intervals use five bracketed
uptime reads as described in [the kernel-start methodology](README.md#kernel-start-versus-ssh-readiness).
SSH is the retrieval channel, not the kernel timing endpoint.

| Trial | Fresh-launch kernel start | Second-boot kernel start | Second-boot observed RUNNING | Second-boot SSH |
| --- | ---: | ---: | ---: | ---: |
| 1 | 38.282–38.584 s | 9.731–10.011 s | 5.290 s | 16.984 s |
| 2 | 36.124–36.482 s | 7.697–8.089 s | 2.776 s | 14.780 s |
| 3 | 31.744–32.033 s | 7.596–7.881 s | 2.876 s | 14.741 s |
| 4 | 34.462–34.859 s | 10.219–10.609 s | 5.296 s | 17.668 s |
| 5 | 29.997–30.365 s | 9.666–10.052 s | 5.059 s | 16.860 s |

Fresh-launch times start at `LaunchInstance`; second-boot times start just
before the probe thread and `START` request. Stop time is excluded. The paired
fresh-launch medians were 34.462–34.859 s to kernel start and 44.611 s to SSH.
On second boot, the kernel logged its `/init` handoff after 0.859–0.900 s;
this remains a stock-initramfs baseline, not a custom lightweight-init test.
OCI `RUNNING` again preceded the inferred kernel origin in every trial.

Validation confirmed five distinct instance OCIDs, changed boot IDs, `STOPPED`
before each `START`, and all five instances `TERMINATED` afterward with boot-volume
deletion requested. The final sweep found zero leftovers. Cloud-init reported
`done`; systemd completed startup but reported `degraded`, as in the older test
below. This does not certify overall guest health. Graceful stops took
38.668–122.536 s (median 97.881 s), so these results apply to **already stopped**
instances, not the whole stop/start cycle. Long-idle starts and tail latency
were not measured.

Reproduce with five sequential invocations of `script/oci_restart_benchmark.py
--iterations 1`, each with a separate `--output`, using the RWX/network setup
below. The script now records kernel samples and raw dmesg on both boots.
The five raw JSON files, including instance IDs, are in `second-boot-raw-results.zip`
in the [measurement thread](https://ampcode.com/threads/T-01a13742-e07b-7161-b8ef-73cdf253b968).

## September 19, 2026: historical single-VM test

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
