# E5 VM ↔ Object Storage throughput

**Eight concurrent transfers achieved approximately 2 Gbps in both directions,
effectively matching the tested VM's reported 2 Gbps network allocation.**
Transferring eight 1 GiB objects took about 34 seconds per direction.

## Environment

| Setting | Value |
| --- | --- |
| Shape | `VM.Standard.E5.Flex` |
| CPU | 2 OCPUs / 4 vCPUs |
| Memory | 8 GB |
| OCI-reported network bandwidth | 2 Gbps |
| Region | `us-chicago-1` |
| Image | `Oracle-Linux-9.8-2026.08.14-0` |
| Storage | Same-region OCI Standard Object Storage |
| Network path | Public regional HTTPS endpoint via internet gateway |
| Recorded start time | `2026-09-17T16:14:19Z` (benchmark clock) |

## Results

Each phase transferred eight 1 GiB objects (8 GiB total). Upload and download
phases ran separately, with three repetitions at each concurrency level.
The table reports medians; ranges show the lowest and highest phase throughput.

| Direction | Concurrent transfers | Median throughput | Throughput range | Median payload rate | Median time for 8 GiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| Upload | 1 | 1.005 Gbps | 0.997–1.041 Gbps | 119.9 MiB/s | 68.35 s |
| Download | 1 | 1.278 Gbps | 1.215–1.280 Gbps | 152.3 MiB/s | 53.78 s |
| Upload | 8 | **1.999 Gbps** | 1.913–2.019 Gbps | **238.3 MiB/s** | **34.38 s** |
| Download | 8 | **2.000 Gbps** | 1.993–2.005 Gbps | **238.5 MiB/s** | **34.35 s** |

Eight concurrent transfers roughly doubled upload throughput and increased
download throughput by about 57% compared with one transfer at a time.
For this VM size and storage path, eight connections were sufficient to
approach the reported network allocation in both directions.

### Individual phase measurements

| Concurrent transfers | Repetition | Upload Gbps | Download Gbps | Upload seconds | Download seconds |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 0.997 | 1.278 | 68.94 | 53.78 |
| 1 | 2 | 1.041 | 1.215 | 66.01 | 56.57 |
| 1 | 3 | 1.005 | 1.280 | 68.35 | 53.68 |
| 8 | 1 | 2.019 | 1.993 | 34.03 | 34.48 |
| 8 | 2 | 1.913 | 2.005 | 35.93 | 34.27 |
| 8 | 3 | 1.999 | 2.000 | 34.38 | 34.35 |

## Methodology and validation

- curl performed HTTPS PUT and GET requests from the E5 guest.
- Uploads read a 1 GiB random, incompressible RAM-backed file, reused as the
  payload for eight distinct objects. Data generation was outside timing.
- Downloads discarded bodies to `/dev/null`, excluding local disk writes.
- Aggregate throughput was total successful payload bytes divided by phase
  wall time, including connection setup. Gbps uses decimal bits per second;
  GiB and MiB use binary bytes.
- Each transfer required an HTTP 2xx response and the expected byte count.
  Final stored-object sizes and `Content-MD5` checksums matched the source.
- All 12 phases completed successfully: 96 GiB total, split equally between
  uploads and downloads. The temporary VM, boot volume, objects, bucket, and
  preauthenticated request were cleaned up.

## Scope and limitations

This is an end-to-end HTTPS/Object Storage benchmark, not a raw NIC capacity
test. It does not measure simultaneous full-duplex traffic, cross-region
traffic, another cloud's blob storage, or durable local file writes. Downloaded
bodies were byte-counted but not independently hashed, and repeated downloads
may benefit from service-side caching.

The measurements cover one VM and three repetitions per configuration, not
long-duration sustained throughput or variability across hosts. Larger E5
configurations can have different network allocations; these results should
not be treated as a limit for the entire E5 family.

## Reproduce

From a checkout with RWX access and the repository's OCI vault configured:

```sh
rwx sandbox exec -- python script/oci_network.py
rwx sandbox exec -- python script/oci_transfer_benchmark.py
```

The defaults reproduce the configuration above and write
`transfer-benchmark.json` with per-transfer timings and VM metadata. See the
[benchmark instructions](README.md#object-storage-throughput) for parameters,
credentials, and cleanup behavior.
