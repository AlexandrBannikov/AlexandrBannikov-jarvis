#!/usr/bin/env python3
"""Offline scanner for secrets accidentally added to the Git worktree/index."""

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TRACKED_PREFIXES = (
    ".env",
    "etc/jarvis/",
    "/etc/jarvis/",
)
PATTERNS = (
    (
        "non-empty secret assignment",
        re.compile(
            r"^[ \t]*(?:TELEGRAM_BOT_TOKEN|OPENAI_API_KEY)"
            r"[ \t]*=[ \t]*[^#\s].*$",
            re.MULTILINE,
        ),
    ),
    ("OpenAI key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    (
        "Telegram bot token",
        re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    ),
    (
        "private key header",
        re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    ),
    (
        "Authorization bearer value",
        re.compile(r"Authorization\s*:\s*Bearer\s+\S+", re.IGNORECASE),
    ),
)


@dataclass(frozen=True, slots=True)
class SecretFinding:
    path: str
    line: int
    kind: str


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )


def tracked_paths() -> list[str]:
    output = _git("ls-files", "-z").stdout
    return [
        item.decode("utf-8", errors="replace")
        for item in output.split(b"\0")
        if item
    ]


def scan_text(path: str, text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(SecretFinding(path, line, kind))
    return findings


def scan_repository() -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    paths = tracked_paths()
    for path in paths:
        normalized = path.lstrip("./")
        if normalized == ".env" or any(
            normalized.startswith(prefix)
            for prefix in FORBIDDEN_TRACKED_PREFIXES[1:]
        ):
            findings.append(SecretFinding(path, 0, "forbidden tracked path"))
            continue
        file_path = PROJECT_ROOT / path
        if not file_path.is_file():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        findings.extend(scan_text(path, text))

    staged = _git("diff", "--cached", "--unified=0", "--no-color").stdout
    staged_text = staged.decode("utf-8", errors="replace")
    added = "\n".join(
        line[1:]
        for line in staged_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    findings.extend(scan_text("<staged changes>", added))
    return findings


def main() -> int:
    findings = scan_repository()
    if findings:
        for finding in findings:
            location = (
                f"{finding.path}:{finding.line}"
                if finding.line
                else finding.path
            )
            print(f"[FAIL] {location}: {finding.kind}")
        print(f"Secret scan: FAILED ({len(findings)} finding(s))")
        return 1
    print("Secret scan: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
