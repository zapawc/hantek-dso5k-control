"""Documented JSON command line for local qualification and offline inspection."""
import argparse
import json
from pathlib import Path
import sys

from . import __version__
from ._file_replay import FileReplayError, replay_file
from ._retrieve import CONFIRMATIONS, FILE_BYTES, retrieve
from ._session import qualify
from ._settings import settings
from ._waveform import analyze


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hantek", allow_abbrev=False)
    parser.add_argument("--version", action="version", version=__version__)
    cmds = parser.add_subparsers(dest="command", required=True)
    q = cmds.add_parser("qualify", allow_abbrev=False).add_subparsers(dest="action", required=True)
    readonly = q.add_parser("readonly", allow_abbrev=False)
    readonly.add_argument("--output", required=True, type=Path)
    readonly.add_argument("--confirm-single-client", action="store_true")
    readonly.add_argument("--confirm-settled-history", action="store_true")
    offline = cmds.add_parser("offline", allow_abbrev=False).add_subparsers(dest="action", required=True)
    cfg = offline.add_parser("settings", allow_abbrev=False)
    cfg.add_argument("input", type=Path)
    wave = offline.add_parser("waveform", allow_abbrev=False)
    wave.add_argument("input", type=Path)
    wave.add_argument("--output", type=Path, required=True)
    wave.add_argument("--sample-interval", type=float, required=True)
    wave.add_argument("--volts-per-division", type=float, required=True)
    wave.add_argument("--probe-factor", type=float, required=True)
    wave.add_argument("--vertical-position-counts", type=int, required=True)
    file_replay = offline.add_parser("file-replay", allow_abbrev=False)
    file_replay.add_argument("input", type=Path)
    file_replay.add_argument("--filename", required=True)
    file_replay.add_argument("--expected-bytes", type=int, required=True)
    file_replay.add_argument("--expected-sha256", required=True)
    file_replay.add_argument("--output", type=Path, required=True)
    files = cmds.add_parser("files", allow_abbrev=False).add_subparsers(dest="action", required=True)
    fetch = files.add_parser("retrieve", allow_abbrev=False)
    fetch.add_argument("--instrument-path", required=True)
    fetch.add_argument("--output", required=True, type=Path)
    fetch.add_argument("--reference", required=True, type=Path)
    fetch.add_argument("--evidence-dir", required=True, type=Path)
    fetch.add_argument("--max-bytes", type=int, default=FILE_BYTES)
    fetch.add_argument("--transfer-timeout", type=int, default=12)
    fetch.add_argument("--experimental-ch2", action="store_true",
                       help="select the offline-tested CH2 candidate (live use requires separate approval)")
    fetch.add_argument("--expected-sha256", help="reviewed independent reference digest, required for experimental CH2")
    for confirmation in CONFIRMATIONS:
        fetch.add_argument("--confirm-" + confirmation, action="store_true")
    for name, actions in {"device": ["identify"], "settings": ["read"], "clock": ["read", "compare", "sync"],
                          "capture": ["single"]}.items():
        family = cmds.add_parser(name, allow_abbrev=False).add_subparsers(dest="action", required=True)
        for action in actions:
            cmd = family.add_parser(action, allow_abbrev=False)
            if action == "sync":
                cmd.add_argument("--confirm", action="store_true")
            elif action == "single":
                cmd.add_argument("--channels")
                cmd.add_argument("--output", type=Path)
            else:
                cmd.add_argument("--json", action="store_true")
    screen = cmds.add_parser("screenshot", allow_abbrev=False)
    screen.add_argument("--output", type=Path)
    screen.add_argument("--metadata", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "qualify":
            output = qualify(args.output, confirmed=args.confirm_single_client,
                             history_confirmed=args.confirm_settled_history)
            code = output["exit_code"]
        elif args.command == "files":
            confirmations = [name for name in CONFIRMATIONS
                             if getattr(args, "confirm_" + name.replace("-", "_"))]
            output = retrieve(args.instrument_path, args.output, args.reference, args.evidence_dir,
                              max_bytes=args.max_bytes, timeout=args.transfer_timeout, confirmations=confirmations,
                              experimental_ch2=args.experimental_ch2, expected_sha256=args.expected_sha256)
            code = output["exit_code"]
        elif args.command == "offline":
            if args.action == "settings":
                with args.input.open("rb") as handle:
                    output = settings(handle.read(209))
            elif args.action == "waveform":
                output = analyze(args.input, args.output, args.sample_interval, args.volts_per_division,
                                 args.probe_factor, args.vertical_position_counts)
                output = {"schema_version": "1.0", **output}
            else:
                output = replay_file(args.input, args.output, args.filename, args.expected_bytes,
                                     args.expected_sha256)
                output = {"schema_version": "1.0", "command": "offline file-replay", **output}
            code = 0
        else:
            output = {"schema_version": "1.0", "status": "UNAVAILABLE",
                      "command": args.command, "action": getattr(args, "action", None)}
            code = 6
    except FileReplayError as exc:
        output = {"schema_version": "1.0", "command": "offline file-replay", "status": "ERROR",
                  "error": exc.reason, "detail": str(exc)}
        code = 4
    except (OSError, ValueError) as exc:
        output = {"schema_version": "1.0", "status": "ERROR", "error": type(exc).__name__}
        code = 4
    print(json.dumps(output, ensure_ascii=True))
    if code:
        print(output.get("error") or output.get("status") or "See session integrity results.", file=sys.stderr)
    return code
