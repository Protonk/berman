"""Assemble, compress and verify an edition's payload; write it into or compare it with the tree.

The ZIP codec preserves bytes, Unix modes, symlink targets and directory entries under a
namespace; berman_preserve packages single recordings with it. Nothing here keeps state
between builds.
"""
import io
import json
import os
from pathlib import Path
import stat
import zipfile

from berman.archive.select import PREFIX, SHA

LEGACY_ARCHIVE = "archive/stewart-runs-001-002.zip"


def assemble(build, generated):
    """Every ZIP member as {path: (bytes, mode)}; checksums.sha256 covers this payload only."""
    root = build.root
    payload = {}
    for entry in build.entries:
        path = entry["path"]
        mode = int(entry["mode"], 0)
        if entry["type"] == "directory":
            payload[path + "/"] = (b"", stat.S_IFDIR | mode)
        elif entry["type"] == "symlink":
            payload[path] = (os.fsencode(entry["target"]), stat.S_IFLNK | mode)
        else:
            payload[path] = ((root / path).read_bytes(), stat.S_IFREG | mode)
    support = ['source-layout.json', *build.documents]
    if build.edition.name == 'stewart':
        support = ["source-layout.json", "AGENTS.md", ".gitignore", *build.documents]
        support += [p.relative_to(root).as_posix() for p in sorted((root / "tools").glob("*.py"))]
        support += [name for name in ("berman", "berman.json", "BERMAN.md", "MAINTENANCE.md") if (root / name).is_file() and name not in support]
        for folder in ("recipes", "connections", "plans"):
            support += [p.relative_to(root).as_posix() for p in sorted((root / folder).rglob("*")) if p.is_file()]
        support += [p.relative_to(root).as_posix() for p in sorted((root / "hooks/verification").glob("*.json"))]
        support += [p.relative_to(root).as_posix() for p in sorted((root / "tools/development_launchers").glob("*.py"))]
        support += [p.relative_to(root).as_posix() for p in sorted((root / "hooks").glob("*.json"))]
    for path in support:
        payload[path] = ((root / path).read_bytes(), stat.S_IFREG | stat.S_IMODE((root / path).stat().st_mode))
    if build.subset:
        payload["source-layout.json"] = ((json.dumps(build.layout, ensure_ascii=False, indent=2) + "\n").encode(), payload["source-layout.json"][1])
    for path, data in generated.items():
        payload[path] = (data, stat.S_IFREG | 0o644)
    lines = [f"{SHA(data)}  {path}" for path, (data, mode) in sorted(payload.items()) if stat.S_ISREG(mode)]
    payload["checksums.sha256"] = (("\n".join(lines) + "\n").encode("utf-8"), stat.S_IFREG | 0o644)
    return payload


def make_zip(payload, prefix=None):
    """The namespace is an edition setting; Stewart-compatible editions keep stewart-analysis/."""
    prefix = PREFIX if prefix is None else prefix
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, (data, mode) in sorted(payload.items()):
            info = zipfile.ZipInfo(prefix + path, date_time=(2026, 10, 22, 0, 0, 0))
            info.create_system = 3
            info.external_attr = mode << 16
            if stat.S_ISDIR(mode):
                info.external_attr |= 0x10
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    return stream.getvalue()


def verify_zip(data, payload, prefix=None):
    prefix = PREFIX if prefix is None else prefix
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        expected = {prefix + path for path in payload}
        if len(archive.infolist()) != len(expected) or set(archive.namelist()) != expected:
            raise ValueError("ZIP membership does not match the payload")
        for path, (content, mode) in payload.items():
            info = archive.getinfo(prefix + path)
            if archive.read(info) != content or info.external_attr >> 16 != mode:
                raise ValueError(f"ZIP bytes or mode differ: {path}")


def destinations(build, generated, payload, zipped):
    """{path: bytes} this edition places in the tree: reading aids only for the cumulative edition."""
    outputs = dict(generated, **{"checksums.sha256": payload["checksums.sha256"][0]}) if build.tree else {}
    outputs[build.archive_path] = zipped
    # The ZIP's own checksum stays outside it, avoiding a self-referential checksum.
    sidecar = "archive/SHA256SUMS" if build.tree else build.archive_path + ".sha256"
    outputs[sidecar] = f"{SHA(zipped)}  {Path(build.archive_path).name}\n".encode()
    return outputs


def obsolete(build):
    """The retired first archive, superseded by the cumulative edition."""
    if not build.tree:
        return []
    legacy = build.root / LEGACY_ARCHIVE
    return [legacy] if legacy.exists() or legacy.is_symlink() else []


def compare(build, outputs, stale, payload):
    """Check that the tree already holds these outputs; write nothing."""
    if stale:
        raise ValueError(f"Obsolete generated files remain: {stale}")
    for path, data in outputs.items():
        if not (build.root / path).is_file() or (build.root / path).read_bytes() != data:
            raise ValueError(f"Generated file is missing or stale: {path}")
    verify_zip((build.root / build.archive_path).read_bytes(), payload, build.edition.prefix)


def write(root, path, data):
    target = root / path
    if path.startswith(("runs/", ".git/")) or target.is_symlink() or any(p.is_symlink() for p in target.parents if p != root.parent):
        raise ValueError(f"Unsafe generated destination: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def apply(build, outputs, stale):
    """Write these outputs into the tree and retire stale generated files."""
    for path, data in sorted(outputs.items()):
        write(build.root, path, data)
    for path in stale:
        if path.is_symlink():
            raise ValueError(f"Unexpected symlink at obsolete generated file: {path}")
        path.unlink()
