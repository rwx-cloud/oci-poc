# E6 AX VM boot and termination

**E6 AX VMs answered SSH in a median 42.6 seconds from the launch request,
with a range of 40.9–42.9 seconds across three launches.** OCI reported
`RUNNING` at a median 29.9 seconds, before SSH was available.

## Environment

| Setting | Value |
| --- | --- |
| Shape | `VM.Standard.E6.Ax.Flex` |
| CPU | 2 OCPUs / 4 vCPUs |
| Memory | 8 GB |
| Boot volume | 50 GB |
| Region | `us-ashburn-1` |
| Availability domains | AD-1, AD-3, AD-1, in iteration order |
| Image | `Oracle-Linux-9.8-2026.08.14-0` |
| Recorded first launch time | `2026-09-18T21:36:19Z` (benchmark clock) |
| Workflow | `.rwx/oci-benchmark.yml` |

The [RWX run](https://cloud.rwx.com/rwx/runs/2d223f2db45c49059a7c46b11e08ef8b)
succeeded, including the network, benchmark, and sweep tasks. Its benchmark
task publishes `benchmark.json` with per-iteration measurements and metadata.
AD-2 did not list this shape, so the benchmark skipped it before launching.

## Results

All phase offsets below are measured from the launch request. Termination
duration is measured separately, from the termination request to observed
`TERMINATED`; it is not the total instance lifetime.

| Phase | Minimum | Median | Maximum |
| --- | ---: | ---: | ---: |
| Launch API returned | 1.272 s | 1.295 s | 2.670 s |
| Public IP assigned | 6.733 s | 9.285 s | 11.304 s |
| OCI `RUNNING` | 28.629 s | 29.889 s | 30.421 s |
| SSH answering | **40.942 s** | **42.605 s** | **42.938 s** |
| Terminate API returned | 42.801 s | 44.435 s | 44.688 s |
| OCI `TERMINATED` | 90.103 s | 92.997 s | 93.466 s |
| Termination duration | 45.940 s | 49.180 s | 51.365 s |

### Individual measurements

| Iteration | Domain | OCI `RUNNING` | SSH answering | Termination duration | SSH probe window |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | AD-1 | 30.421 s | 42.605 s | 49.180 s | 0.264 s |
| 2 | AD-3 | 29.889 s | 42.938 s | 45.940 s | 0.272 s |
| 3 | AD-1 | 28.629 s | 40.942 s | 51.365 s | 0.264 s |

## Methodology and validation

- The full RWX workflow ran with its default three iterations, 2 OCPUs,
  and 8 GB RAM; only the shape was overridden.
- Each iteration launched one fresh VM, waited for its public IP, observed
  control-plane state, and probed SSH independently from state polling.
- SSH readiness requires an `SSH-` identification string, not just an
  accepted TCP connection. An authenticated SSH guest report also succeeded
  on all three VMs.
- Lifecycle state polling sleeps one second between checks. SSH probes sleep
  0.25 seconds between unsuccessful attempts; the largest recorded gap
  between the last failed probe start and successful probe start was 0.272 s.
  These are externally observed, polled timings rather than guest event timestamps.
- All three VMs reached `TERMINATED`. Termination requested deletion of their
  boot volumes, and the final sweep found zero remaining active instances
  tagged for this run. The reusable benchmark network remains in place.
- The shape-availability filter was checked in RWX with mocked supported and
  unsupported domains: supported domains retain rotation order, and no
  supported domain fails before launch. The live run also verified skipping AD-2.

## Scope and limitations

SSH readiness is not completion of every guest startup task. All three guest
reports said systemd startup was still in progress; kernel uptime at the
authenticated check was 10.77–10.89 seconds.

This is a small sample spanning two availability domains, not a tail-latency
study or a comparison against E5 under matched conditions. It does not measure
application readiness, Object Storage throughput, or raw network capacity.
The earlier exploratory three-VM run and the canceled follow-up sandbox run
are excluded from these results. The canceled run's one VM was separately
confirmed terminated.

## Reproduce

From a checkout with RWX access and the repository's OCI vault configured for
the Ashburn account:

```sh
rwx run .rwx/oci-benchmark.yml --wait \
  --init shape=VM.Standard.E6.Ax.Flex
```

The benchmark checks shape availability per domain before rotating through
supported domains. Listing a shape does not guarantee launch capacity.
Use `--init iterations=5` for more samples. See the
[benchmark instructions](README.md#running-it) for credentials and parameters.
