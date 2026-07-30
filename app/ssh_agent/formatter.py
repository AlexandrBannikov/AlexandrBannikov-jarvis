"""Concise Russian formatting of already-sanitized service results."""

from __future__ import annotations

from .redaction import redact_secrets
from .service_models import SSHServiceResult


def _size(value: object) -> str:
    number = int(value or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if number < 1024 or unit == "ТБ":
            rendered = f"{number:.1f}".replace(".", ",") if unit in {"ГБ", "ТБ"} else str(round(number))
            return f"{rendered} {unit}"
        number /= 1024
    return "0 Б"


def _percent(value: object) -> str:
    try:
        return f"{float(value):.1f}".replace(".", ",")
    except (TypeError, ValueError):
        return "?"


def format_result(result: SSHServiceResult) -> str:
    if not result.success and not result.partial:
        return result.message or "Не удалось выполнить безопасный SSH-запрос."
    data = result.data
    op = result.operation
    if op == "disk_usage":
        text = f"Диск {data.get('mount_point', '/')}: {data.get('percent_used', '?')}% использовано"
    elif op == "memory_usage":
        text = f"Память: {_size(data.get('used_bytes'))} из {_size(data.get('total_bytes'))}"
    elif op == "uptime":
        text = f"Uptime: {data.get('readable', 'неизвестно')}"
    elif op == "load_average":
        text = f"Load average: {data.get('load_1', '?')} / {data.get('load_5', '?')} / {data.get('load_15', '?')}"
    elif op == "top_processes":
        sort_label = "CPU" if data.get("sort_by") == "cpu" else "памяти"
        processes = data.get("processes", ())
        lines = [
            f"На сервере {result.server_alias} больше всего используют {sort_label}:"
        ]
        lines.extend(
            f"{index}. {item.get('command', 'неизвестно')} — "
            f"{_percent(item.get('cpu_percent'))}% CPU, "
            f"{_percent(item.get('memory_percent'))}% памяти"
            for index, item in enumerate(processes, start=1)
        )
        if not processes:
            lines.append("Активные процессы не найдены.")
        text = "\n".join(lines)
    elif op == "service_status":
        text = (
            f"Сервис: {result.service_name}\n\n"
            f"• состояние: {data.get('active_state') or 'неизвестно'}\n"
            f"• режим: {data.get('sub_state') or 'неизвестно'}\n"
            f"• автозапуск: {data.get('unit_file_state') or 'неизвестно'}\n"
            f"• результат: {data.get('result') or 'неизвестно'}"
        )
    elif op == "project_git_status":
        text = (
            f"Проект: {result.project_alias}\n"
            f"• ветка: {data.get('branch', 'неизвестно')}\n"
            f"• рабочее дерево: {'чистое' if data.get('clean') else 'есть изменения'}"
        )
    elif op == "service_recent_logs":
        text = f"Последние строки {result.service_name}:\n" + "\n".join(data.get("lines", ()))
    elif op in {"server_summary", "project_summary"}:
        text = _format_composite(result)
    elif op == "list_servers":
        text = "Серверы:\n" + "\n".join(f"• {item['alias']}" for item in data.get("items", ()))
    elif op == "list_projects":
        text = f"Проекты на {result.server_alias}:\n" + "\n".join(
            f"• {item['alias']}" for item in data.get("items", ())
        )
    else:
        text = result.message or "Запрос выполнен."
    if result.truncated:
        text += "\n\n⚠ Вывод сокращён."
    if result.partial:
        text += "\n\n⚠ Часть данных недоступна."
    return redact_secrets(text)[:64_000]


def _format_composite(result: SSHServiceResult) -> str:
    children = result.data.get("results", ())
    by_op = {child.operation: child for child in children if child.success}
    if result.operation == "server_summary":
        lines = [f"Сервер: {result.server_alias}"]
        if child := by_op.get("uptime"):
            lines.append(f"• uptime: {child.data.get('readable')}")
        if child := by_op.get("load_average"):
            lines.append(f"• load average: {child.data.get('load_1')} / {child.data.get('load_5')} / {child.data.get('load_15')}")
        if child := by_op.get("memory_usage"):
            lines.append(f"• память: {_size(child.data.get('used_bytes'))} из {_size(child.data.get('total_bytes'))}")
        if child := by_op.get("disk_usage"):
            lines.append(f"• диск /: {child.data.get('percent_used')}%")
        return "\n\n".join((lines[0], "\n".join(lines[1:])))
    lines = [f"Сервер: {result.server_alias}", f"Проект: {result.project_alias}", "", "Git:"]
    if child := by_op.get("project_git_status"):
        lines.extend((f"• ветка: {child.data.get('branch')}",
                      f"• рабочее дерево: {'чистое' if child.data.get('clean') else 'есть изменения'}"))
    if child := by_op.get("project_last_commit"):
        lines.append(f"• последний коммит: {child.data.get('short_hash')} — {child.data.get('subject')}")
    services = [child for child in children if child.operation == "service_status" and child.success]
    if services:
        lines.extend(("", "Сервисы:"))
        lines.extend(f"• {child.service_name}: {child.data.get('active_state')} / {child.data.get('sub_state')}" for child in services)
    return "\n".join(lines)
