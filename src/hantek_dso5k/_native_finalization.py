"""Recoverable finalization for the supervised native adapter."""

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import json
import os
import stat

from ._host import Blocked
from ._retrieve import file_record


def _encode(record):
    return (json.dumps(record, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _write_synced(stream, data):
    stream.write(data)
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def _file_id(info):
    return info.st_dev, info.st_ino


def create_record(path, record):
    data = _encode(record)
    with path.open("xb") as stream:
        _write_synced(stream, data)
        return _file_id(os.fstat(stream.fileno())), data


def _verify(stream, identity):
    expected_id, data = identity
    if (_file_id(os.fstat(stream.fileno())) != expected_id
            or stream.read(len(data) + 1) != data):
        raise Blocked("Finalization marker was replaced or modified")


def _verify_path(path, identity):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or _file_id(info) != identity[0]:
        raise Blocked("Finalization marker path changed")
    with path.open("rb") as stream:
        _verify(stream, identity)


@contextmanager
def _windows_record(path, identity, *, writable=False):
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    _verify_path(path, identity)
    # READ | DELETE, optional WRITE, share reads only, OPEN_EXISTING, no reparse traversal.
    access = 0xC0010000 if writable else 0x80010000
    handle = kernel.CreateFileW(str(path), access, 1, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if writable else os.O_RDONLY) | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(fd, "r+b" if writable else "rb") as stream:
        _verify(stream, identity)
        yield kernel, handle, stream


def _retire_marker(marker, identity):
    if os.name != "nt":
        # Filesystem-only simulations. The live adapter requires Windows.
        _verify_path(marker, identity)
        marker.unlink()
        return
    with _windows_record(marker, identity) as (kernel, handle, _):
        delete = ctypes.c_ubyte(1)
        if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete)):
            raise ctypes.WinError(ctypes.get_last_error())
    if os.path.lexists(marker):
        raise Blocked("Marker retirement could not be confirmed")


def _rename_guard(guard, identity, data, final):
    if os.name != "nt":
        _verify_path(guard, identity)
        with guard.open("r+b") as stream:
            _verify(stream, identity)
            stream.seek(0)
            _write_synced(stream, data)
        if os.path.lexists(final):
            raise Blocked("Final manifest destination already exists")
        _verify_path(guard, (identity[0], data))
        guard.rename(final)
        return

    filename = str(final).encode("utf-16-le")

    class RenameInfo(ctypes.Structure):
        _fields_ = [("replace", wintypes.DWORD), ("root", wintypes.HANDLE),
                    ("length", wintypes.DWORD), ("filename", ctypes.c_byte * (len(filename) + 2))]

    info = RenameInfo()
    info.length = len(filename)
    ctypes.memmove(ctypes.addressof(info) + RenameInfo.filename.offset, filename, len(filename))
    # The file handle protects the verified guard through its no-replace rename.
    with _windows_record(guard, identity, writable=True) as (kernel, handle, stream):
        stream.seek(0)
        _write_synced(stream, data)
        if not kernel.SetFileInformationByHandle(handle, 3, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())


def prepare(evidence, marker, marker_identity, guard, result):
    """Run under the cooperative lease. Install an admission fence before retiring the marker."""
    result["finalization"] = "PENDING"
    identity = create_record(guard, {
        "session_id": result["session_id"], "source_commit": result["source_commit"],
        "evidence": str(evidence), "state": "FINALIZING",
    })
    result["admission_guard"] = "RETAINED"
    result["marker"] = "IDENTITY_UNCONFIRMED"
    _verify_path(marker, marker_identity)
    result["marker"] = "RETAINED"
    for path in evidence.iterdir():
        if path.is_file():
            with path.open("r+b") as stream:
                os.fsync(stream.fileno())
    result["artifacts"] = {path.name: file_record(path) for path in evidence.iterdir() if path.is_file()}
    pending = evidence / "native_manifest.pending.json"
    create_record(pending, dict(result, status="FINALIZING"))
    result["marker"] = "RETIREMENT_UNCONFIRMED"
    try:
        _retire_marker(marker, marker_identity)
    except (OSError, Blocked, KeyboardInterrupt):
        try:
            _verify_path(marker, marker_identity)
        except (OSError, Blocked):
            pass
        else:
            result["marker"] = "RETAINED"
        raise
    result["marker"] = "RETIRED"
    return identity


def publish(evidence, marker, guard, identity, result):
    """Commit both final evidence and admission clearance in one same-volume rename."""
    final = evidence / "native_manifest.json"
    if os.path.lexists(marker):
        raise Blocked("An interruption marker appeared during finalization")
    completed = dict(result, status=result["acquisition_outcome"], marker="CLEARED",
                     admission_guard="CLEARED", finalization="COMPLETE")
    data = _encode(completed)
    staged = identity[0], data
    try:
        _rename_guard(guard, identity, data, final)
        result.update(completed)
    except (OSError, KeyboardInterrupt) as exc:
        # Cancellation may arrive immediately after the atomic operation committed.
        try:
            _verify_path(final, staged)
        except (OSError, Blocked):
            raise exc from None
        result.update(completed)
