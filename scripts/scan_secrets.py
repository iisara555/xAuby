#!/usr/bin/env python3
"""Dependency-free secret scanner for tracked files and git history.

The scanner is intentionally conservative about output: it reports locations and
masked fingerprints, never secret values.
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


MAX_FILE_BYTES = 5 * 1024 * 1024
HISTORY_MAX_FILE_BYTES = 10 * 1024 * 1024

SKIP_PATH_PARTS = (
    ".git/",
    ".pytest_cache/",
    ".venv/",
    "__pycache__/",
    "build/",
    "dist/",
    "node_modules/",
    "venv/",
)

SKIP_EXTENSIONS = {
    ".csv",
    ".db",
    ".docx",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".webp",
    ".xlsx",
    ".zip",
}

KNOWN_SECRET_RE = re.compile(
    r"(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{30,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-(?:proj-)?[A-Za-z0-9_-]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{20,}"
    r"|\d{7,12}:[A-Za-z0-9_-]{30,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    r"|https://discord(?:app)?\.com/api/webhooks/[0-9]{15,25}/[A-Za-z0-9_-]{30,}"
)

ASSIGNMENT_RE = re.compile(
    r"(?ix)"
    r"(?P<key>\b[A-Z0-9_.-]*"
    r"(?:api[_-]?key|api[_-]?secret|secret[_-]?key|secret|token|password|passwd|pwd|"
    r"passphrase|private[_-]?key|webhook|client[_-]?secret|access[_-]?token|refresh[_-]?token)"
    r"[A-Z0-9_.-]*\b)"
    r"\s*[:=]\s*"
    r"(?P<quote>[\"']?)"
    r"(?P<value>[^\"'\s#,;]+)"
)

ENV_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
PLACEHOLDER_RE = re.compile(
    r"(?ix)^("
    r"|none|null|nil|false|true|0|1"
    r"|x+|\*+|.*x{4,}.*"
    r"|<.*>|\{.*\}|\$\{?[A-Z0-9_]+\}?"
    r"|your[_-]?.*|change[_-]?me|changeme|replace(?:[_-]?with)?[_-]?.*"
    r"|example|sample|dummy|fake|mock|test|testing|fixture[_-]?.*|fresh.*|stale.*|[ksp][_-]?value"
    r"|test[_-]?.*|fake[_-]?.*|dummy[_-]?.*|server[_-]only.*"
    r"|client[_-]?secret(?:[_-]?\d+)?"
    r"|placeholder|redacted|masked|abc123|secret|token|password"
    r")$"
)


@dataclass(frozen=True)
class Finding:
    scope: str
    path: str
    line: int
    kind: str
    severity: str
    key: str | None
    fingerprint: str
    blob: str | None = None


def run_git(args: Sequence[str], *, input_bytes: bytes | None = None) -> bytes:
    proc = subprocess.run(
        ["git", *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.stdout


def git_zlines(args: Sequence[str]) -> list[str]:
    return [
        item.decode("utf-8", "replace")
        for item in run_git(args).split(b"\0")
        if item
    ]


def should_skip_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if any(part in normalized for part in SKIP_PATH_PARTS):
        return True
    return Path(normalized).suffix.lower() in SKIP_EXTENSIONS


def looks_binary(data: bytes) -> bool:
    return b"\0" in data[:4096]


def entropy(value: str) -> float:
    if not value:
        return 0.0
    return -sum(
        (value.count(char) / len(value)) * math.log2(value.count(char) / len(value))
        for char in set(value)
    )


def normalize(value: str) -> str:
    return value.strip().strip("\"'").strip(",;")


def is_placeholder(value: str) -> bool:
    normalized = normalize(value)
    if not normalized:
        return True
    if ENV_VAR_NAME_RE.fullmatch(normalized) and "_" in normalized:
        return True
    if IDENTIFIER_RE.fullmatch(normalized):
        return True
    if PLACEHOLDER_RE.fullmatch(normalized):
        return True
    if normalized.startswith(("os.environ", "getenv(", "env(", "settings.", "`", "${", "<")):
        return True
    if normalized.startswith(("?", "/usr/", "/bin/", "/sbin/")):
        return True
    if normalized.endswith((">`", "`")):
        return True
    if any(ord(char) > 127 for char in normalized):
        return True
    return len(normalized) < 6 and not normalized.lstrip("-").isdigit()


def is_code_expression(value: str) -> bool:
    normalized = normalize(value)
    return any(char in normalized for char in ("(", ")", "[", "]", "{", "}"))


def assignment_severity(key: str, value: str) -> str | None:
    key_lower = key.lower()
    normalized = normalize(value)
    if is_placeholder(normalized) or is_code_expression(normalized):
        return None
    if key_lower.endswith(".required") and normalized.lower() in {"true", "false"}:
        return None
    if key_lower in {"token", "tokens"} and normalized.lstrip("-").isdigit():
        return None
    if normalized in {"[]", "{}", "-", "|", ">"}:
        return None
    if any(
        marker in key_lower
        for marker in ("secret", "token", "password", "passphrase", "private", "webhook")
    ):
        return "high" if len(normalized) >= 12 else "medium"
    if "api" in key_lower and "key" in key_lower:
        return "high" if len(normalized) >= 16 or entropy(normalized) >= 3.2 else "medium"
    return "medium"


def fingerprint(value: str) -> str:
    normalized = normalize(value)
    if len(normalized) <= 8:
        return f"len={len(normalized)}"
    return f"len={len(normalized)} prefix={normalized[:2]}...suffix={normalized[-2:]}"


def scan_text(scope: str, path: str, text: str, *, blob: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for match in KNOWN_SECRET_RE.finditer(line):
            value = match.group(0)
            if is_placeholder(value):
                continue
            findings.append(
                Finding(
                    scope=scope,
                    path=path,
                    line=line_no,
                    kind="known_secret_literal",
                    severity="high",
                    key=None,
                    fingerprint=fingerprint(value),
                    blob=blob[:12] if blob else None,
                )
            )

        for match in ASSIGNMENT_RE.finditer(line):
            key = match.group("key")
            value = match.group("value")
            severity = assignment_severity(key, value)
            if not severity:
                continue
            findings.append(
                Finding(
                    scope=scope,
                    path=path,
                    line=line_no,
                    kind="sensitive_assignment",
                    severity=severity,
                    key=key,
                    fingerprint=fingerprint(value),
                    blob=blob[:12] if blob else None,
                )
            )
    return findings


def scan_worktree_paths(paths: Iterable[str], scope: str, max_bytes: int = MAX_FILE_BYTES) -> list[Finding]:
    findings: list[Finding] = []
    for path_text in paths:
        if should_skip_path(path_text):
            continue
        path = Path(path_text)
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > max_bytes:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if looks_binary(data):
            continue
        findings.extend(scan_text(scope, path_text, data.decode("utf-8", "replace")))
    return findings


def tracked_files() -> list[str]:
    return git_zlines(["ls-files", "-z"])


def all_candidate_files() -> list[str]:
    files = run_git(["ls-files", "-co", "--exclude-standard", "-z"]).split(b"\0")
    return [item.decode("utf-8", "replace") for item in files if item]


def history_blobs(max_bytes: int = HISTORY_MAX_FILE_BYTES) -> list[tuple[str, str]]:
    object_rows = run_git(["rev-list", "--objects", "--all"]).decode("utf-8", "replace").splitlines()
    objects: list[str] = []
    path_by_oid: dict[str, str] = {}
    seen: set[str] = set()
    for row in object_rows:
        parts = row.split(" ", 1)
        oid = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        if oid in seen or not path or should_skip_path(path):
            continue
        seen.add(oid)
        objects.append(oid)
        path_by_oid[oid] = path

    if not objects:
        return []

    metadata = run_git(
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input_bytes=("\n".join(objects) + "\n").encode("utf-8"),
    ).decode("utf-8", "replace")
    blobs: list[tuple[str, str]] = []
    for row in metadata.splitlines():
        parts = row.split()
        if len(parts) < 3:
            continue
        oid, object_type, size_text = parts[:3]
        if object_type != "blob":
            continue
        try:
            size = int(size_text)
        except ValueError:
            continue
        if size > max_bytes:
            continue
        blobs.append((oid, path_by_oid.get(oid, "")))
    return blobs


def scan_history() -> list[Finding]:
    findings: list[Finding] = []
    for oid, path in history_blobs():
        data = run_git(["cat-file", "blob", oid])
        if looks_binary(data):
            continue
        findings.extend(
            scan_text(
                "history",
                path,
                data.decode("utf-8", "replace"),
                blob=oid,
            )
        )
    return findings


def dedupe(findings: Iterable[Finding]) -> list[Finding]:
    unique: list[Finding] = []
    seen: set[tuple[object, ...]] = set()
    for finding in findings:
        key = (
            finding.scope,
            finding.path,
            finding.line,
            finding.kind,
            finding.severity,
            finding.key,
            finding.fingerprint,
            finding.blob,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def format_finding(finding: Finding) -> str:
    blob = f" blob={finding.blob}" if finding.blob else ""
    key = f" key={finding.key}" if finding.key else ""
    return (
        f"{finding.scope}:{finding.path}:{finding.line}: "
        f"{finding.severity} {finding.kind}{key} {finding.fingerprint}{blob}"
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tracked",
        action="store_true",
        help="scan tracked files in the current working tree",
    )
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="scan tracked and untracked non-ignored files",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help="scan blobs reachable from all git refs",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if not (args.tracked or args.all_files or args.history):
        args.tracked = True

    findings: list[Finding] = []
    if args.tracked:
        findings.extend(scan_worktree_paths(tracked_files(), "tracked"))
    if args.all_files:
        findings.extend(scan_worktree_paths(all_candidate_files(), "worktree"))
    if args.history:
        findings.extend(scan_history())

    findings = sorted(
        dedupe(findings),
        key=lambda item: (
            {"tracked": 0, "worktree": 1, "history": 2}.get(item.scope, 9),
            {"high": 0, "medium": 1}.get(item.severity, 9),
            item.path,
            item.line,
            item.kind,
        ),
    )

    if findings:
        print("Potential secrets found. Values are masked; inspect and remove/rotate as needed.")
        for finding in findings:
            print(format_finding(finding))
        return 1

    print("No potential secrets found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
