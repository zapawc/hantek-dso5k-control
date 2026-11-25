"""Interactive terminal client for the Digital Workbench Hantek profile."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from urllib.error import HTTPError

from ._dw_profile import EXT, LIMITS
from .service_client import Client


def plot_trace(values, width=64):
    low, high = min(values), max(values)
    ramp = ".:-=+*#%@"
    if high == low:
        return ramp[len(ramp) // 2] * min(len(values), width)
    return "".join(ramp[round((values[i] - low) * (len(ramp) - 1) / (high - low))]
                   for i in range(0, len(values), max(1, (len(values) + width - 1) // width)))


def plot_artifact(data, media):
    if media == "text/csv":
        values = [float(line.split(",")[-1]) / 1000
                  for line in data.decode("ascii").splitlines() if line.strip().lstrip(",")[0:1] not in ("", "#")]
        return [f"Frozen CSV [V] {plot_trace(values)}"] if values else []
    lines = []
    for channel in json.loads(data)["channels"]:
        values, units = None, "display codes"
        if channel.get("engineering"):
            values, units = channel["engineering"]["values"], "V (configured scale)"
        elif channel.get("raw"):
            values = channel["raw"]["values"]
        if values:
            lines.append(f"{channel['channel']} [{units}] {plot_trace(values)}")
    return lines


def monitor(config, refresh, *, once=False):
    client = Client(config, profile=True)
    stop = Event()
    output = Lock()
    previous = None
    last_job = None

    def display():
        nonlocal previous
        status = client.json("status")
        settings = client.json("settings")["values"] or {}
        samples = client.json("telemetry")["samples"]
        local = status["extensions"][EXT]
        count, now = local["poll_count"], monotonic()
        rate = None if previous is None else (count - previous[0]) / max(.001, now - previous[1])
        previous = count, now
        with output:
            print(f"Service {status['state']} | USB {'connected' if status['connected'] else 'disconnected'}"
                  f" | acquisition state {settings.get('trigger_state_code', 'unknown')}"
                  f" | sample updates/s {'--' if rate is None else f'{rate:.1f}'}"
                  f" | active job {local['active_job'] or '--'}")
            if status.get("fault"):
                print(status["fault"]["detail"])
            if last_job:
                job = client.json(f"jobs/{last_job}")
                print(f"Most recent job {last_job} {job['state']} {job.get('error', {}).get('code', '')}")
            for item in samples:
                number = "unavailable" if item["value"] is None else f"{item['value']:.5g} {item['unit']}"
                age = "-" if item["age_s"] is None else f"{item['age_s']:.1f}s"
                print(f"  {item['channel_id'].upper()} {item['quantity']:<10} {number:<15} "
                      f"{item['source']} | {age}{' stale' if item['stale'] else ''}"
                      f" {((item['error'] or {}).get('code', ''))}")

    if once:
        display()
        return

    def refresh_display():
        while not stop.is_set():
            try:
                display()
            except (OSError, ValueError) as exc:
                with output:
                    print(f"Monitor request failed ({exc})")
            stop.wait(refresh)

    watcher = Thread(target=refresh_display, daemon=True)
    watcher.start()
    print("Enter snapshot, single [auto|native|frozen] [yes], run yes, stop, plot [job-id], shutdown or quit.")
    try:
        while True:
            fields = input().strip().split()
            if not fields:
                continue
            action, *options = fields
            if action == "quit":
                return
            if action == "shutdown":
                client.json("shutdown", {})
                return
            try:
                if action == "plot":
                    job_id = options[0] if options else last_job
                    if not job_id:
                        print("Capture a waveform first.")
                        continue
                    job = client.json(f"jobs/{job_id}")
                    for entry in job.get("result", {}).get("artifacts", []):
                        media = entry["media_type"]
                        name = entry["extensions"][EXT]["name"]
                        if media != "text/csv" and name != "waveform.json":
                            continue
                        data = client.request(f"jobs/{job_id}/artifacts/{entry['artifact_id']}")
                        if len(data) != entry["size_bytes"] or sha256(data).hexdigest() != entry["sha256"]:
                            raise ValueError("Artifact failed size or SHA-256 check")
                        for line in plot_artifact(data, media):
                            print(f"{name} {line}")
                    continue
                if action == "snapshot" and not options:
                    kind, args = "snapshot", {}
                elif action == "single" and all(word in ("auto", "native", "frozen", "yes") for word in options):
                    kind = "triggered"
                    args = {"path": next((word for word in options if word != "yes"), "auto"),
                            "allow_overwrite": "yes" in options}
                elif (action == "run" and options == ["yes"]) or (action == "stop" and not options):
                    kind, args = "run-state", {"running": action == "run", "allow_overwrite": action == "run"}
                else:
                    print("Unknown command or missing overwrite consent. Use 'run yes' to start Run.")
                    continue
                accepted = client.json("jobs", {"kind": f"hantek.{kind}", "arguments": args,
                                                "timeout_s": LIMITS[kind][1]})
                last_job = accepted["job_id"]
                print(f"Queued {last_job}")
            except HTTPError as exc:
                print(exc.read(4096).decode("utf-8", errors="replace"))
            except (OSError, ValueError) as exc:
                print(f"Request failed ({exc})")
    finally:
        stop.set()
        watcher.join()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--refresh", type=float, default=2.)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not .5 <= args.refresh <= 60.:
        parser.error("Refresh interval must be 0.5 to 60 seconds")
    try:
        monitor(args.config, args.refresh, once=args.once)
    except (EOFError, KeyboardInterrupt):
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
