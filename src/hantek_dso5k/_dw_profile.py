"""Digital Workbench v0.1 adapter over the existing service state."""

from hashlib import sha256

PREFIX = "/dw/v0.1"
EXT = "com.github.zapawc.hantek"
LIMITS = {"snapshot": (6, 45), "triggered": (65, 120), "run-state": (6, 30), "external": (95, 180)}
STATES = {"STARTING": "starting", "MONITORING": "ready", "CAPTURING": "busy",
          "STOPPING": "stopping", "STOPPED": "stopping", "FAULT": "fault"}
ARGUMENTS = {
    "snapshot": {},
    "triggered": {"path": {"enum": ["auto", "native", "frozen"], "default": "auto"},
                  "allow_overwrite": {"type": "boolean", "default": False}},
    "run-state": {"running": {"type": "boolean"},
                  "allow_overwrite": {"type": "boolean", "default": False}},
    "external": {"event_id": {"type": "integer", "minimum": 1, "maximum": 65535},
                 "path": {"enum": ["auto", "native", "frozen"], "default": "auto"},
                 "allow_overwrite": {"type": "boolean", "default": False}},
}


def problem(status, code, message):
    titles = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
              409: "Conflict", 422: "Unprocessable Content", 503: "Service Unavailable",
              500: "Internal Server Error"}
    return {"type": "about:blank", "title": titles[status], "status": status, "code": code, "detail": message}


def submission(payload, parse_job):
    if not isinstance(payload, dict) or set(payload) != {"kind", "arguments", "timeout_s"}:
        raise ValueError("Provide kind, arguments and timeout_s")
    kind, arguments, budget = payload["kind"], payload["arguments"], payload["timeout_s"]
    if not isinstance(kind, str) or not kind.startswith("hantek.") or kind[7:] not in LIMITS:
        raise ValueError("Unsupported job kind")
    name = kind[7:]
    if not isinstance(arguments, dict) or arguments.keys() - ARGUMENTS[name].keys():
        raise ValueError("Unexpected job arguments")
    minimum, maximum = LIMITS[name]
    if type(budget) not in (int, float) or not minimum <= budget <= maximum:
        raise ValueError(f"timeout_s must be between {minimum} and {maximum} for this job")
    return parse_job({"kind": name, **arguments}), budget


def status(service):
    state = service.status()
    result = {"state": STATES[state["state"]], "connected": state["connected"],
              "extensions": {EXT: {key: state[key] for key in (
                  "state", "poll_count", "resume_count", "active_job", "queued_job",
                  "uptime_s", "worker_done", "polling_suspended")}}}
    if state["error"]:
        result["fault"] = problem(503, "instrument_fault", "Instrument session requires reconciliation")
    return result


def series(channel, quantity, source, unit):
    return {"series_id": f"{channel.lower()}.{quantity}.{source}", "channel_id": channel.lower(),
            "quantity": quantity, "source": source, "unit": unit}


def capabilities(service):
    external = service.external_fixture is not None
    required = {"run-state": ["running"], "external": ["event_id"]}
    return {"channels": [{"channel_id": ch.lower(), "label": ch} for ch in ("CH1", "CH2")],
            "series": [series(*item) for item in service.series()],
            "jobs": [{"kind": f"hantek.{name}", "arguments_schema": {
                "type": "object", "properties": properties, "additionalProperties": False,
                **({"required": required[name]} if name in required else {}),
            }, "cancel_supported": False, "min_timeout_s": LIMITS[name][0],
                "max_timeout_s": LIMITS[name][1]} for name, properties in ARGUMENTS.items()
                     if name != "external" or external],
            "extensions": {EXT: {"triggered_paths": ["auto", "native", "frozen"],
                                  "capture_profiles": ["retained-compensation-4k"]
                                  + (["acq12-supervised-external-4k"] if external else []),
                                  "same_shot": "unproven", "external_arm_ready_qualified": external}}}


def settings(service):
    raw = service.current_settings()
    return {"observed_at": raw["observed_at"] if raw else None,
            "configuration_id": raw["stable_sha256"] if raw else None, "values": raw}


def telemetry(service):
    samples = []
    for row in service.latest(all_sources=True)["measurements"]:
        prior = row["last_good"]
        failure = row["error"]
        validity = {"zero_unverified": "ambiguous", "invalid_display_value": "invalid",
                    "clipped": "invalid"}.get(failure, "unknown")
        samples.append({
            **series(row["channel"], row["measurement"], row["source"], row["unit"]),
            "value": row["value"], "availability": "available" if row["available"] else "unavailable",
            "validity": "valid" if row["available"] else validity, "observed_at": row["observed_at"],
            "age_s": row["transport_age_s"], "stale": row["stale"], "device_freshness": "unknown",
            "configuration_id": row["configuration"]["stable_sha256"],
            "error": problem(503, failure, "No usable measurement for this observation") if failure else None,
            "last_valid": ({"value": prior["value"], "observed_at": prior["observed_at"],
                            "age_s": prior["age_s"], "configuration_id": prior["configuration"]["stable_sha256"]}
                           if prior else None),
            "extensions": {EXT: {"raw_display": row["raw"], "configuration": row["configuration"],
                                  **row.get("waveform", {})}},
        })
    return {"samples": samples}


def artifact_map(job):
    known = {}
    for name, metadata in job.get("result", {}).get("artifacts", {}).items():
        if not (name in ("waveform.json", "external-event.json") or name.endswith(".csv")
                or (name.startswith("frozen_") and name.endswith("_measurements.json"))):
            continue
        artifact_id = sha256(f"{job['id']}:{name}".encode()).hexdigest()[:32]
        media = "text/csv" if name.endswith(".csv") else "application/json"
        version = ("1.0" if name == "waveform.json" else "hantek-external-event-1" if name == "external-event.json"
                   else "hantek-device-csv-1"
                   if name.endswith(".csv") else "hantek-frozen-summary-1")
        known[artifact_id] = (name, {"artifact_id": artifact_id, "size_bytes": metadata["bytes"],
                                    "sha256": metadata["sha256"], "media_type": media,
                                    "schema_version": version, "extensions": {EXT: {"name": name}}})
    return known


def job_view(job):
    view = {"job_id": job["id"], "state": job["state"].lower()}
    if result := job.get("result"):
        view["result"] = {"acquisition": result.get("acquisition_outcome", "unknown"),
                          "restoration": result.get("restoration_outcome", "not_required"),
                          "artifacts": [entry for _, entry in artifact_map(job).values()],
                          "extensions": {EXT: {"method": result.get("method"), "same_shot": "unproven",
                                               **({"common_event": result["common_event"]}
                                                  if "common_event" in result else {})}}}
    error = job.get("error") or job.get("result", {}).get("error")
    if error:
        code = {"JobTimeout": "job_timeout", "Unsupported": "operation_conflict"}.get(
            error["type"], "operation_failed")
        state = {"operation_conflict": 409, "job_timeout": 503}.get(code, 500)
        view["error"] = problem(state, code, "See the service evidence for the outcome")
    return view
