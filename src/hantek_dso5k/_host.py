"""Host-only ownership assessment. USB operations live in a separate module."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from hashlib import sha256
import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys

MUTEX = r"Global\Hantek_049F_505A_RearUsb"
TARGET = "USB\\VID_049F&PID_505A\\"
APPS = {"openhantek.exe", "openhantek6022.exe", "hantek.exe", "hantekdso.exe", "dso5102p.exe"}
SCRIPTS = {"run-qualification.ps1", "run-observation.ps1", "run-viewer.ps1",
           "viewer.py", "autocapture.py", "live_wrapper.py", "rootfs_preflight.py", "validate_usb.py"}
PYTHON = re.compile(r"(?:pythonw?(?:\d+(?:\.\d+)*)?|py)\.exe$")
FORWARDER = re.compile(r"^(?:usbipd?|virtualhere|vhusbd\w*|flexihub|usbredirect\w*|qemu[\w-]*|vmware\w*|vbox\w*|virtualbox)\.exe$")
TARGET_TOKEN = re.compile(r"(?i)\b049f:505a\b|VID_049F&PID_505A\b|\bvendorid\s*=\s*(?:0x)?049f\b.*\bproductid\s*=\s*(?:0x)?505a\b")


class Blocked(RuntimeError):
    pass


def normalized(path):
    return ntpath.normcase(ntpath.normpath(path or ""))


def verify_ancestry(rows, own_pid, own_executable):
    indexed = {row["pid"]: row for row in rows}
    if len(indexed) != len(rows):
        raise Blocked("Duplicate process identity in census")
    own = indexed.get(own_pid)
    expected = (own_executable,) if isinstance(own_executable, str) else own_executable
    if not own or not own.get("exe") or normalized(own["exe"]) not in {normalized(p) for p in expected}:
        raise Blocked("Cannot establish the executing process identity")
    chain = {own_pid}
    child = own
    while parent := indexed.get(child["ppid"]):
        # A reused PID is a new process. Older timestamps are required before exclusion.
        if not parent.get("exe") or not parent.get("created") or not child.get("created"):
            break
        if parent["created"] >= child["created"] or parent["pid"] in chain:
            break
        chain.add(parent["pid"])
        child = parent
    return chain


def invocation_reason(row):
    image = ntpath.basename(row.get("exe") or row.get("name") or "").lower()
    args = row.get("argv") or []
    tail = [arg.lower() for arg in args[1:]]
    if FORWARDER.fullmatch(image) and TARGET_TOKEN.search(" ".join(tail)):
        return "forwarder explicitly references target VID/PID"
    if image in APPS:
        return "known Hantek application executable"
    if PYTHON.fullmatch(image) and tail:
        index = 0
        while index < len(tail) and tail[index] in ("-b", "-u", "-i", "-s", "-e", "-x", "-3", "-3.11"):
            index += 1
        command = tail[index:]
        if command and ntpath.basename(command[0]) in SCRIPTS:
            return "Python directly invokes known Hantek client script"
        if command[:2] == ["-m", "hantek_dso5k"]:
            if command[2:4] == ["qualify", "readonly"]:
                return "Python directly invokes Hantek qualification"
            if command[2:4] in (["files", "retrieve"], ["files", "retrieve-pair"]):
                return "Python directly invokes Hantek retrieval"
    if image in ("powershell.exe", "pwsh.exe"):
        for index, arg in enumerate(tail):
            if arg in ("-command", "-c"):
                direct = re.match(r"""(?i)^\s*(?:&\s*)?(?:'([^']+\.ps1)'|"([^"]+\.ps1)"|([^\s;&|]+\.ps1))(?=\s|$)""",
                                  " ".join(tail[index + 1:]))
                if direct and ntpath.basename(next(value for value in direct.groups() if value)) in SCRIPTS:
                    return "PowerShell directly invokes known Hantek client script"
                break
            if arg in ("-encodedcommand", "-enc"):
                break
            if arg in ("-file", "-f"):
                if index + 1 < len(tail) and ntpath.basename(tail[index + 1]) in SCRIPTS:
                    return "PowerShell directly invokes known Hantek client script"
                break
    return None


def inspect_processes(rows, own_pid, own_executable):
    chain = verify_ancestry(rows, own_pid, own_executable)
    findings = []
    for row in rows:
        image = ntpath.basename(row.get("exe") or row.get("name") or "")
        reason = invocation_reason(row)
        is_forwarding = bool(reason and reason.startswith("forwarder"))
        if row["pid"] in chain and not is_forwarding:
            state = "verified_launcher_ancestry"
            reason = "executing process or verified older parent"
        elif reason:
            state = "conflict"
        elif re.search(r"(?i)hantek|fw-cap", " ".join(row.get("argv") or [])):
            state = "reference_only"
            reason = "project reference without a direct known client invocation"
        else:
            continue
        exe = row.get("exe")
        findings.append({"pid": row["pid"], "parent_pid": row["ppid"], "executable": image,
                         "image_path_sha256": sha256(normalized(exe).encode()).hexdigest() if exe else None,
                         "classification": state, "reason": reason})
    return findings


def assess_host(snapshot, own_pid, own_executable):
    processes = snapshot["processes"]
    findings = inspect_processes(processes, own_pid, own_executable)
    result = {"process_count": len(processes), "unavailable_command_lines": sum(
        not row.get("argv") for row in processes), "findings": findings}
    devices = snapshot["devices"]
    result["available_instances"] = len(devices)
    result["driver"] = devices[0].get("service") if len(devices) == 1 else None
    result["assignment"] = snapshot["assignment"]
    if any(f["classification"] == "conflict" for f in findings):
        result["blocked"] = "Concrete client or target-specific USB forwarding found"
    elif len(devices) != 1 or not (devices[0].get("instance") or "").upper().startswith(TARGET) or (
            (devices[0].get("service") or "").casefold() != "winusb" or devices[0].get("status") != "OK"):
        result["blocked"] = "Expected one healthy local WinUSB target"
    elif snapshot["assignment"] not in ("not_shared", "no_usbip_service"):
        result["blocked"] = "Device assignment is shared, attached or uncertain"
    else:
        result["blocked"] = None
    return result


def split_command_line(value):
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    split = shell.CommandLineToArgvW
    split.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    values = split(value, ctypes.byref(count))
    if not values:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [values[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(ctypes.cast(values, wintypes.HLOCAL))


def host_snapshot():
    if sys.platform != "win32":
        raise Blocked("WinUSB qualification requires Windows")
    ps = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    helper = Path(__file__).with_name("_host_snapshot.ps1")
    result = subprocess.run([str(ps), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-File", str(helper)], capture_output=True, encoding="utf-8", timeout=35)
    if result.returncode:
        raise Blocked("Windows process/device census failed")
    snapshot = json.loads(result.stdout)
    for row in snapshot["processes"]:
        command = row.pop("command", None)
        row["argv"] = split_command_line(command) if command else []
    return snapshot


@contextmanager
def lease():
    if sys.platform != "win32":
        raise Blocked("WinUSB qualification requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, MUTEX)
    if not handle:
        raise Blocked("Could not open the cooperative USB lease")
    held = False
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        held = status in (0, 0x80)
        if status == 0x80:
            raise Blocked("Prior owner abandoned the lease. Command history is uncertain.")
        if status != 0:
            raise Blocked("Cooperative USB lease unavailable")
        yield
    finally:
        released = not held or kernel.ReleaseMutex(handle)
        closed = kernel.CloseHandle(handle)
        if not released or not closed:
            raise Blocked("Lease cleanup failed")
