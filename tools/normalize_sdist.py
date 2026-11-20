"""Normalize a setuptools sdist using only Python's standard library."""

import argparse
import gzip
import os
from pathlib import Path, PureWindowsPath
import tarfile


def checked_members(source):
    members = {}
    for member in source:
        name = member.name
        parts = name.split("/")
        if any(not part or part in (".", "..") or part.endswith((" ", "."))
               or PureWindowsPath(part).is_reserved() for part in parts):
            raise ValueError(f"unsafe archive path: {name!r}")
        if any(c in name for c in '\\:*?"<>|') or any(ord(c) < 32 for c in name):
            raise ValueError(f"unsafe archive path: {name!r}")
        key = name.casefold()
        if key in members:
            raise ValueError(f"duplicate archive path: {name!r}")
        if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE) or member.sparse:
            raise ValueError(f"unsupported archive member: {name!r}")
        members[key] = member
    roots = {name.split("/", 1)[0] for name in members}
    if len(roots) != 1:
        raise ValueError("expected one source distribution root directory")
    for name, member in members.items():
        parent, _, _ = member.name.rpartition("/")
        if parent:
            ancestor = members.get(parent.casefold())
            if not ancestor or not ancestor.isdir() or ancestor.name != parent:
                raise ValueError(f"missing directory for archive member: {member.name!r}")
        elif not member.isdir():
            raise ValueError(f"expected a root directory: {name!r}")
    return sorted(members.values(), key=lambda member: member.name)


def normalize(source_path, output_path, epoch):
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError("SOURCE_DATE_EPOCH is outside the gzip timestamp range")
    with tarfile.open(source_path, "r:gz") as source:
        members = checked_members(source)
        with output_path.open("xb") as output, \
             gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=epoch) as compressed, \
             tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for item in members:
                info = tarfile.TarInfo(item.name)
                info.mtime = epoch
                if item.isdir():
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    archive.addfile(info)
                else:
                    info.type = tarfile.REGTYPE
                    info.mode = 0o644
                    info.size = item.size
                    with source.extractfile(item) as content:
                        archive.addfile(info, content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        normalize(args.source, args.output, int(os.environ["SOURCE_DATE_EPOCH"]))
    except (KeyError, OSError, ValueError, tarfile.TarError) as exc:
        parser.exit(1, f"sdist normalization failed: {exc}\n")


if __name__ == "__main__":
    main()
