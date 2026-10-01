#!/usr/bin/env python3
"""Reject private or generated content from the staged Git tree."""

from __future__ import annotations

import re
import argparse
import subprocess
import sys
from pathlib import PurePosixPath

MAX_BYTES = 10 * 1024 * 1024
DENIED_DIRS = {
    "data", "raw", "recordings", "beamformed", "outputs", "results", "runs",
    "manifests", "private", "local", "scratch", "artifacts", "models",
    "checkpoints", ".venv", "venv", "__pycache__",
}
DENIED_SUFFIXES = {
    ".wav", ".wave", ".flac", ".mp3", ".ogg", ".pcm", ".raw",
    ".sqlite", ".db", ".pem", ".key", ".p12", ".pfx",
    ".hex", ".ehex", ".bin", ".jar", ".class", ".tflite",
    ".onnx", ".pt", ".pth", ".h5", ".keras", ".safetensors",
}
SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9_]{25,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_+/=-]{20,}"),
]


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def paths_for(base: str | None) -> list[str]:
    args = ["diff", "--name-only", "-z", "--diff-filter=ACMR"]
    args += [base, "HEAD"] if base else ["--cached"]
    raw = git(*args)
    return [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]


def check(path: str, base: str | None) -> list[str]:
    problems: list[str] = []
    p = PurePosixPath(path)
    folded = path.casefold()
    if any(part.casefold() in DENIED_DIRS for part in p.parts):
        problems.append("private/generated directory")
    if p.suffix.casefold() in DENIED_SUFFIXES or folded.endswith((".sqlite-wal", ".sqlite-shm", ".db-wal", ".db-shm")):
        problems.append("private/generated file type")
    if p.name.casefold() == ".env" or folded.endswith(".env") or ".env." in folded or folded.endswith("config.txt"):
        if p.name != ".env.example":
            problems.append("local configuration")
    if "credential" in folded or "service-account" in folded or "sol_handoff.local" in folded:
        problems.append("private name")
    meta = (git("ls-tree", "HEAD", "--", path) if base else git("ls-files", "-s", "--", path)).decode("utf-8", "replace")
    if meta.startswith("120000") or meta.startswith("160000"):
        problems.append("symlink or submodule")
    content = git("show", f"HEAD:{path}" if base else f":{path}")
    if len(content) > MAX_BYTES:
        problems.append("file exceeds 10 MiB")
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        problems.append("secret-like content")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Compare committed HEAD against this trusted base commit")
    args = parser.parse_args()
    paths = paths_for(args.base)
    failures = [(p, check(p, args.base)) for p in paths]
    failures = [(p, errors) for p, errors in failures if errors]
    for path, errors in failures:
        print(f"BLOCKED {path}: {', '.join(errors)}", file=sys.stderr)
    if failures:
        return 1
    print(f"Repository policy passed for {len(paths)} changed files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
