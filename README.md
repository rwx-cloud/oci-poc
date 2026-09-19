# OCI POC

A proof of concept for driving Oracle Cloud Infrastructure from RWX, plus a
benchmark of how long OCI takes to boot and terminate a small VM.

Everything runs on RWX. Nothing here needs to run on your laptop.

## Layout

| Path | What it is |
| --- | --- |
| `.rwx/oci-auth.yml` | Confirms the `oci-poc` vault credentials authenticate against the tenancy |
| `.rwx/oci-benchmark.yml` | Provisions the network, runs the boot/terminate benchmark, sweeps leftovers |
| `.rwx/sandbox.yml` | A cloud sandbox with the OCI CLI and credentials already wired up |
| `script/oci_common.py` | Client construction, tagging, logging |
| `script/oci_network.py` | Idempotent VCN / subnet / gateway / route table / security list |
| `script/oci_benchmark.py` | The measured launch → boot → terminate loop |
| `script/oci_sweep.py` | Terminates POC instances left behind by an aborted run |

## Credentials

Authentication uses an OCI API signing key stored in the RWX `oci-poc` vault:

| Vault entry | Maps to |
| --- | --- |
| `vars.user` | `OCI_CLI_USER` — the user OCID |
| `vars.tenancy` | `OCI_CLI_TENANCY` — the tenancy OCID, which is also the root compartment |
| `vars.fingerprint` | `OCI_CLI_FINGERPRINT` — fingerprint of the uploaded public key |
| `vars.region` | `OCI_CLI_REGION` |
| `vars.public-key` | Unused at runtime; kept for reference |
| `secrets.private-key` | Written to a file at task start and pointed at by `OCI_CLI_KEY_FILE` |

The private key is never written into the workspace, so it does not end up in a
cached task layer. It is also marked `cache-key: excluded` so rotating it does
not invalidate unrelated caches.

Everything runs in the root compartment by default. Set `OCI_COMPARTMENT_ID` to
scope it elsewhere.

## Running it

Confirm authentication:

```sh
rwx run .rwx/oci-auth.yml --wait
```

Run the benchmark:

```sh
rwx run .rwx/oci-benchmark.yml --wait
rwx run .rwx/oci-benchmark.yml --wait --init iterations=5
rwx run .rwx/oci-benchmark.yml --wait --init ocpus=1 --init memory-gb=8
```

It is also registered as a dispatch trigger, `oci-vm-boot-benchmark`, so it can
be started from the RWX UI or API without the CLI.

Poke at the tenancy interactively:

```sh
rwx sandbox exec -- oci compute shape list --compartment-id "$OCI_CLI_TENANCY" --output table
rwx sandbox exec -- python script/oci_sweep.py --all-runs
```

## What the benchmark measures

Each iteration starts one wall clock and records offsets from it:

| Mark | Meaning |
| --- | --- |
| `launch_api_returned` | `LaunchInstance` accepted the request |
| `public_ip_assigned` | The VNIC has a public address |
| `state_running` | The control plane reports `lifecycle_state: RUNNING` |
| `ssh_reachable` | `sshd` returned its identification string on port 22 |
| `terminate_api` | `TerminateInstance` accepted the request |
| `state_terminated` | The control plane reports `lifecycle_state: TERMINATED` |

`ssh_reachable` is the number that matters for "how long until the VM is
usable." `state_running` fires well before the guest has finished booting, so
treating it as boot time understates things by roughly 15 seconds.

The check waits for the `SSH-` identification string rather than treating a bare
accepted TCP connection as ready, because the connection can be accepted before
`sshd` is actually serving.

### Measurement resolution

Every mark is polled, so each is an upper bound on the true time.

| Mark | Cadence | Overstated by at most |
| --- | --- | --- |
| `state_running` | 1.0s sleep + one `GetInstance` | ~1.2s |
| `public_ip_assigned` | 1.0s sleep + three API calls | ~1.5s |
| `ssh_reachable` | 0.25s sleep + 1.0s connect timeout | reported per run |
| `state_terminated` | 1.0s sleep + one `GetInstance` | ~1.2s |

A failed SSH probe returns in about 10ms, not the full connect timeout: OCI
rejects connections to the closed port rather than dropping them, so the probe
gets a refusal instead of waiting for a timeout. Resolution is therefore set by
the sleep between probes, not by the connect timeout. Measured across 5
iterations: 50-60 failed probes per boot, uncertainty 0.26s.

The SSH probe runs on its own thread starting the moment the public IP exists,
rather than after `RUNNING` is observed, so there is no window in which `sshd`
could come up unwatched. Each iteration records `ssh_probe_window`: the gap
between the start of the last failed probe and the start of the successful one.
`sshd` became reachable somewhere inside that window, so it is the exact upper
bound on how much of the reported number is our own polling latency. The summary
prints the worst case across iterations.

Iterations rotate through availability domains so a single degraded AD does not
dominate the result. Results are written to a `benchmark.json` artifact with
per-iteration detail alongside the summary.

`LaunchInstance` is throttled per user, so two benchmark runs against the same
tenancy at the same time will earn a `429 TooManyRequests`. The script retries
with exponential backoff and restarts its clock on the attempt that succeeds, so
a throttled launch costs wall time but does not corrupt the measurement. Running
the configurations sequentially is still the better idea.

## Shape and CPU count

OCI bills flexible shapes in **OCPUs**. On x86 shapes such as
`VM.Standard.E5.Flex`, one OCPU is a full physical core presented as two vCPUs.
So a machine comparable to a 2 vCPU / 8 GB instance on other clouds is
`--init ocpus=1 --init memory-gb=8`, and `ocpus=2` is a 4 vCPU machine. The
default here is `ocpus=2`; both were measured and boot time is not sensitive to
either.

## Cleanup

`script/oci_benchmark.py` terminates each instance in a `finally` block and on
`SIGTERM`/`SIGINT`. The `sweep` task then runs after the benchmark whether it
succeeded, failed, or was aborted, and terminates anything still tagged with
this run's ID. A task killed outright cannot run its own cleanup, which is what
`sweep` exists for.

To clear out instances from every past run:

```sh
rwx sandbox exec -- python script/oci_sweep.py --all-runs
```

The VCN and subnet are intentionally left in place; they cost nothing and
keeping them means the benchmark never pays for network setup.

## Object Storage throughput

Run an end-to-end network/storage benchmark from a temporary E5 VM:

```sh
rwx sandbox exec -- python script/oci_network.py
rwx sandbox exec -- python script/oci_transfer_benchmark.py
```

Defaults: 2 OCPUs (4 vCPUs), 8 GB RAM, eight 1 GiB objects in a temporary
same-region Standard Object Storage bucket, three repetitions each with one
and eight concurrent transfers. Upload and download phases run separately.
Use `--ocpus`, `--memory-gb`, `--concurrency`, or `--repetitions` to vary the
test. `--size-mib 1 --files 2 --repetitions 1` is a small smoke test.

The guest uses curl over HTTPS to the public regional endpoint through the
existing internet gateway. Uploads read one random, incompressible RAM-backed
file into eight distinct objects; downloads discard data to `/dev/null`.
This excludes disk throughput and data generation from timing. Aggregate
throughput is total successful payload bytes divided by phase wall time,
including connection setup. Each transfer must return HTTP 2xx and the expected
byte count; final stored objects must match the source size and Content-MD5.
This does not hash downloaded bodies or measure durable local file writes.

`transfer-benchmark.json` records individual timings, aggregate Gbps and MiB/s,
and the VM's network bandwidth allocation reported by OCI. This measures the
HTTPS/Object Storage path, not raw NIC capacity or simultaneous full-duplex
throughput. Repeated downloads may benefit from service-side caching.

The VM receives only a two-hour bucket-scoped preauthenticated URL over SSH,
never tenancy credentials. The benchmark terminates the VM and deletes its
boot volume, temporary objects, bucket, and preauthenticated request in cleanup,
including on ordinary errors and SIGINT/SIGTERM. A hard-killed sandbox can
leave resources behind: instances carry the existing POC tags for sweeping,
and temporary buckets have unique `rwx-transfer-` names and POC tags.

## Block-volume provisioning and hot attachment

```sh
rwx sandbox exec -- python script/oci_network.py
rwx sandbox exec -- python script/oci_volume_benchmark.py --iterations 3
```

The benchmark boots one temporary `VM.Standard.E6.Ax.Flex` VM (2 OCPUs,
8 GB RAM), waits for SSH and cloud-init, then creates three fresh 50 GiB
Balanced volumes (10 VPUs/GB). Each volume is attached using paravirtualization
and an explicitly selected available device name. VM startup and device-name
selection are outside the timer. Volumes are detached and deleted between trials.

Measured September 19, 2026 in `us-ashburn-1`, `nivC:US-ASHBURN-AD-1`, with
`Oracle-Linux-9.8-2026.08.14-0`:

| Trial | Create → AVAILABLE | Attach request → ATTACHED | Create → guest read completed |
| --- | ---: | ---: | ---: |
| 1 | 7.223 s | 12.223 s | 19.861 s |
| 2 | 6.645 s | 11.399 s | 18.409 s |
| 3 | 8.024 s | 12.088 s | 20.526 s |
| **Median** | **7.223 s** | **12.088 s** | **19.861 s** |

The guest check reads 4 KiB from the new block device with direct I/O after
OCI reports `ATTACHED`; its timestamp includes SSH connection and command
overhead. These are observed upper bounds, not exact guest device-arrival
times. Lifecycle polling sleeps 0.5 s between API calls, whose latency adds to
the observation delay. No filesystem is created or mounted, and write readiness
and throughput are not measured. Three sequential volumes on one VM in one AD
do not establish tail latency or cross-region performance.

`volume-benchmark.json` records API-return offsets, per-trial timings, resource
IDs, and cleanup status. An initial diagnostic attachment returned no device
path and was cleaned up; it is excluded from the three complete trials above.
Ordinary completion, errors, and SIGINT/SIGTERM trigger cleanup. A hard kill or
cleanup API failure can leave tagged resources behind; the existing instance
sweeper does not delete data volumes, which need separate inspection and cleanup.
