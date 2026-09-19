# E6 standard versus Minimal image boot time

**Minimal reduced median launch-to-SSH time by only 0.761 seconds (1.7%) in
this small sample: 45.242 s versus 46.003 s.** Mean time improved by 3.396 s
(7.3%), driven by one much faster Minimal launch. Minimal consistently finished
guest startup sooner, but these results do not establish a large or reliable
end-to-end launch speedup.

## Environment

| Setting | Value |
| --- | --- |
| Shape | `VM.Standard.E6.Flex` (not E6 AX) |
| CPU | 2 OCPUs / 4 vCPUs |
| Memory | 8 GB |
| Boot volume | 50 GB |
| Region / availability domain | `us-ashburn-1` / `nivC:US-ASHBURN-AD-1` |
| Standard image | `Oracle-Linux-9.8-2026.08.14-0` |
| Minimal image | `Oracle-Linux-9.7-Minimal-2026.01.29-0` |
| Samples | Three fresh instances per image |
| Order | Standard, Minimal, repeated three times; sequential launches |
| Recorded interval | `2026-09-19T01:02:45Z`–`2026-09-19T01:13:19Z` (benchmark clock) |

The comparison ran through `rwx sandbox exec` using the existing
`script/oci_benchmark.py`, with one iteration per invocation. Both variants
used the same network and availability domain. Standard 9.7 was not in the
current E6-compatible image listing, so this compares available images rather
than identical OS releases with only a package-set difference.

Minimal was not listed as compatible with `VM.Standard.E6.Ax.Flex`; both the
image's compatibility entries and shape-filtered image listing were checked.
Regular E6 was used for both sides instead. These are not E6 AX measurements.

## Results

All times below are offsets from the launch request, except guest startup time.

| Metric | Standard | Minimal |
| --- | ---: | ---: |
| Median SSH readiness | 46.003 s | 45.242 s |
| Mean SSH readiness | 46.666 s | 43.269 s |
| SSH readiness range | 45.499–48.495 s | 39.032–45.534 s |
| Median OCI `RUNNING` | 33.225 s | 37.773 s |
| Largest recorded SSH probe window | 0.273 s | 0.273 s |

### Individual measurements

| Pair | Standard `RUNNING` | Minimal `RUNNING` | Standard SSH | Minimal SSH | SSH time saved |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 35.514 s | 37.827 s | 48.495 s | 45.534 s | 2.961 s |
| 2 | 33.225 s | 33.197 s | 45.499 s | 39.032 s | 6.467 s |
| 3 | 26.360 s | 37.773 s | 46.003 s | 45.242 s | 0.761 s |

Minimal was faster in each adjacent pair, but the sample is too small and the
launch timing too variable to predict a dependable saving. The higher Minimal
`RUNNING` median also illustrates why control-plane state is not a guest boot
timestamp and why guest optimizations need not translate directly into equal
end-to-end savings.

## Methodology and validation

- SSH readiness required the server's `SSH-` identification string. Each VM
  also accepted an authenticated SSH connection for its guest report.
- Minimal reported completed systemd startup in **5.869, 5.607, and 5.736 s**.
  Standard reported startup still in progress at the check, with kernel uptime
  **11.46, 11.30, and 11.47 s**. Uptime at login and completed systemd startup
  duration are different measurements; these are not directly comparable totals.
- State polling slept one second between checks. SSH probing slept 0.25 s
  between unsuccessful attempts. The largest recorded gap between the last
  failed probe start and successful probe start was 0.273 s.
- All six instances reached `TERMINATED`, with boot-volume deletion requested.
  The final run-scoped sweep reported `swept 0 instance(s)`. The reusable
  benchmark network remains in place.
- Six individual JSON outputs were retained as `standard-1.json` through
  `standard-3.json` and `minimal-1.json` through `minimal-3.json` in the thread's
  `e6-minimal-comparison` artifacts. Summary statistics were recomputed from
  their per-instance marks in RWX, not averaged from rounded display values.

## Scope and limitations

This measures stock-image SSH readiness, not application readiness. No workload
or application health check was supplied. It does not isolate image size from
kernel, OS version, services, or security defaults, and it does not establish
the benefit of Minimal on E6 AX.

The images were not modified or updated before timing. Oracle documents that
[Minimal disables SELinux enforcement and several services, including auditd](https://docs.oracle.com/en-us/iaas/oracle-linux/oci/minimal-image.htm).
This comparison therefore does **not** demonstrate the same speedup with
equivalent security settings. Production use requires security review and
updates; neither is accounted for in these first-boot timings.

Three samples per image, always standard first within each pair, do not control
for host placement, time effects, or long-tail provisioning delays. The smaller
median change and larger mean change should both be considered rather than
selecting only the more favorable statistic.

## Reproduce

Run in an RWX sandbox with the OCI vault configured for Ashburn. Create the
network with `python script/oci_network.py`, then restrict the generated
`network.json` to AD-1 before invoking the benchmark:

```sh
python -c 'import json; p="network.json"; d=json.load(open(p)); d["availability_domains"]=[a for a in d["availability_domains"] if a.endswith("AD-1")]; assert len(d["availability_domains"])==1; json.dump(d,open(p,"w"))'
ssh-keygen -t ed25519 -N '' -f /tmp/e6-comparison-key
for rep in 1 2 3; do
  for variant in standard minimal; do
    version=9
    if [ "$variant" = minimal ]; then version='9 Minimal'; fi
    python script/oci_benchmark.py \
      --shape VM.Standard.E6.Flex --ocpus 2 --memory-gb 8 \
      --iterations 1 --operating-system-version "$version" \
      --ssh-key /tmp/e6-comparison-key --guest-report \
      --output "$variant-$rep.json" || exit 1
  done
done
python script/oci_sweep.py
rm /tmp/e6-comparison-key /tmp/e6-comparison-key.pub
```

Use a unique `RWX_RUN_ID` for run-scoped cleanup. The measured execution also
installed an exit trap to invoke the sweep on failure. Image selection chooses
the latest compatible image for each OS-version string; future runs may select
different releases. No benchmark source changes were needed for this comparison.
