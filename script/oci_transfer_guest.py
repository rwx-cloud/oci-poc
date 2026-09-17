"""Guest-side curl benchmark. Read short-lived object URLs from stdin, never log them."""

import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time


def main():
    config = json.load(sys.stdin)
    size = config["size"]
    # Reuse an incompressible, RAM-backed source for eight distinct objects.
    # Generating it and hashing it are deliberately outside the timed window.
    with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
        source = os.path.join(directory, "source")
        digest = hashlib.md5()
        with open(source, "wb") as handle:
            remaining = size
            while remaining:
                block = os.urandom(min(1024 * 1024, remaining))
                handle.write(block)
                digest.update(block)
                remaining -= len(block)

        def transfer(direction, url):
            command = [
                "curl", "--silent", "--show-error", "--fail",
                "--connect-timeout", "30", "--max-time", "900",
                "--output", "/dev/null", "--write-out",
                "%{http_code} %{size_upload} %{size_download} %{time_total}",
            ]
            if direction == "upload":
                command += ["--upload-file", source]
            result = subprocess.run(command + [url], capture_output=True, text=True)
            if result.returncode:
                # stderr might include the preauthenticated URL; do not expose it.
                raise RuntimeError(f"curl {direction} failed: exit {result.returncode}")
            status, uploaded, downloaded, seconds = result.stdout.split()
            actual = int(uploaded if direction == "upload" else downloaded)
            if not status.startswith("2") or actual != size:
                raise RuntimeError(f"{direction}: HTTP {status}, {actual} bytes, expected {size}")
            return {"bytes": actual, "seconds": float(seconds), "http_status": status}

        results = []
        for concurrency in config["concurrency"]:
            for repetition in range(1, config["repetitions"] + 1):
                for direction in ("upload", "download"):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                        started = time.monotonic()
                        transfers = list(pool.map(
                            lambda url: transfer(direction, url), config["urls"]
                        ))
                        elapsed = time.monotonic() - started
                    total = sum(t["bytes"] for t in transfers)
                    row = {
                        "direction": direction, "concurrency": concurrency,
                        "repetition": repetition, "seconds": elapsed, "bytes": total,
                        "gbps": total * 8 / elapsed / 1e9,
                        "mib_per_second": total / elapsed / 2**20,
                        "transfers": transfers,
                    }
                    results.append(row)
                    print(json.dumps(row), flush=True)
        print(json.dumps({"source_md5": digest.hexdigest(), "results": results}), flush=True)


if __name__ == "__main__":
    main()
