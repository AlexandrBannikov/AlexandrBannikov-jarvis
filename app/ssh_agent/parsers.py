"""Conservative parsers for the fixed operation output formats."""

from __future__ import annotations

import re
import math


def parse_disk(text: str) -> dict[str, object]:
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines[-1]) < 6:
        raise ValueError
    fields = lines[-1]
    return {"total_bytes": int(fields[1]), "used_bytes": int(fields[2]),
            "available_bytes": int(fields[3]), "percent_used": int(fields[4].rstrip("%")),
            "mount_point": fields[5]}


def parse_memory(text: str) -> dict[str, object]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z_()]+):\s+(\d+)(?:\s+kB)?", line.strip())
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    total = values["MemTotal"]
    available = values.get("MemAvailable", values.get("MemFree"))
    if available is None:
        raise ValueError
    return {"total_bytes": total, "available_bytes": available,
            "used_bytes": max(0, total - available)}


def parse_load(text: str) -> dict[str, object]:
    fields = text.split()
    if len(fields) < 3:
        raise ValueError
    return {"load_1": float(fields[0]), "load_5": float(fields[1]), "load_15": float(fields[2])}


def parse_uptime(text: str) -> dict[str, object]:
    seconds = int(float(text.split()[0]))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    return {"uptime_seconds": seconds, "readable": f"{days} дн. {hours} ч. {minutes} мин."}


def parse_service(text: str) -> dict[str, object]:
    allowed = {"Id", "LoadState", "ActiveState", "SubState", "UnitFileState",
               "Result", "ExecMainStatus"}
    values = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in allowed:
            values[key] = value[:256]
    return {"id": values.get("Id"), "load_state": values.get("LoadState"),
            "active_state": values.get("ActiveState"), "sub_state": values.get("SubState"),
            "unit_file_state": values.get("UnitFileState"), "result": values.get("Result"),
            "exec_main_status": values.get("ExecMainStatus")}


def parse_git_status(text: str) -> dict[str, object]:
    lines = text.splitlines()
    header = lines[0] if lines and lines[0].startswith("## ") else ""
    branch_part = header[3:].split("...", 1)[0].strip() if header else "неизвестно"
    ahead = int(m.group(1)) if (m := re.search(r"ahead (\d+)", header)) else 0
    behind = int(m.group(1)) if (m := re.search(r"behind (\d+)", header)) else 0
    changed = len(lines) - (1 if header else 0)
    return {"branch": branch_part or "неизвестно", "clean": changed == 0,
            "ahead": ahead, "behind": behind, "changed_entries": changed}


def parse_last_commit(text: str) -> dict[str, object]:
    fields = text.strip().split("\x00")
    if len(fields) != 3 or not fields[0]:
        raise ValueError
    return {"short_hash": fields[0][:40], "subject": fields[1][:500], "author_date": fields[2][:64]}


def parse_logs(text: str, max_chars: int = 32_000) -> dict[str, object]:
    bounded = text[:max_chars]
    return {"lines": tuple(bounded.splitlines()), "bounded": len(text) > max_chars}


def parse_processes(text: str, limit: int) -> dict[str, object]:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 30
    ):
        raise ValueError
    processes: list[dict[str, object]] = []
    elapsed_pattern = re.compile(r"(?:\d+-)?(?:\d{2}:)?\d{2}:\d{2}\Z")
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split(maxsplit=5)
        if len(fields) != 6:
            raise ValueError
        pid_text, user, cpu_text, memory_text, elapsed, command = fields
        pid = int(pid_text)
        cpu = float(cpu_text)
        memory = float(memory_text)
        if (
            pid <= 0
            or not user
            or len(user) > 64
            or not math.isfinite(cpu)
            or not math.isfinite(memory)
            or cpu < 0
            or memory < 0
            or elapsed_pattern.fullmatch(elapsed) is None
            or not command
            or len(command) > 64
            or "/" in command
            or any(
                ord(character) < 32 or ord(character) == 127
                for value in (user, command)
                for character in value
            )
        ):
            raise ValueError
        processes.append(
            {
                "pid": pid,
                "user": user,
                "cpu_percent": cpu,
                "memory_percent": memory,
                "elapsed": elapsed,
                "command": command,
            }
        )
        if len(processes) >= limit:
            break
    return {"count": len(processes), "processes": tuple(processes)}


PARSERS = {
    "disk_usage": parse_disk, "memory_usage": parse_memory, "load_average": parse_load,
    "uptime": parse_uptime, "service_status": parse_service,
    "project_git_status": parse_git_status, "project_last_commit": parse_last_commit,
    "service_recent_logs": parse_logs,
}
