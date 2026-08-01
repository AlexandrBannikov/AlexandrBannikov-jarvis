"""Explicit built-in skill definitions for current production capabilities."""

from collections.abc import Callable

from app.config import Config
from app.skills.models import HealthStatus, SkillMetadata
from app.skills.registry import SkillRegistry
from app.tools.registry import ToolRegistry


def _names(registry: ToolRegistry) -> tuple[str, ...]:
    return tuple(tool.name for tool in registry.list_tools())


def _check_memory(manager) -> tuple[HealthStatus, str]:
    if manager is None:
        return HealthStatus.DISABLED, "disabled"
    if manager.storage.validate_schema():
        return HealthStatus.OK, "storage and schema ready"
    return HealthStatus.ERROR, "storage schema is invalid"


def _check_core(tool_registry: ToolRegistry) -> tuple[HealthStatus, str]:
    if "system_info" not in _names(tool_registry):
        return HealthStatus.ERROR, "system_info tool is not registered"
    return HealthStatus.OK, "core tools ready"


def _check_reminders(service, scheduler) -> tuple[HealthStatus, str]:
    if service is None:
        return HealthStatus.DISABLED, "disabled"
    if not service.storage.validate_schema():
        return HealthStatus.ERROR, "storage schema is invalid"
    if scheduler is not None and not scheduler.running:
        return HealthStatus.WARNING, "scheduler is not running"
    return HealthStatus.OK, "storage and scheduler ready"


def _check_ssh(dependencies) -> tuple[HealthStatus, str]:
    if dependencies is None or not dependencies.readiness.enabled:
        return HealthStatus.DISABLED, "disabled"
    if dependencies.readiness.ready:
        return HealthStatus.OK, "SSH readiness passed"
    return HealthStatus.ERROR, dependencies.readiness.code.value

def _check_location(service) -> tuple[HealthStatus, str]:
    if service is None: return HealthStatus.DISABLED, "disabled"
    return (HealthStatus.OK, "storage and resolver ready") if service.storage.validate_schema() else (HealthStatus.ERROR, "storage schema is invalid")


def build_skill_registry(tool_registry: ToolRegistry, config: Config, *, memory_manager=None, reminder_service=None, reminder_scheduler=None, ssh_dependencies=None, location_service=None) -> SkillRegistry:
    names = _names(tool_registry)
    memory_names = tuple(name for name in names if name in {"remember", "forget", "update_memory", "search_memory", "list_project_memory", "remember_fact", "recall_memory", "forget_memory", "update_project_memory", "get_project_memory_status"})
    reminder_names = tuple(name for name in names if name.endswith("_reminder"))
    ssh_names = tuple(name for name in names if name not in memory_names and name.startswith(("list_ssh_", "get_server_", "get_service_", "get_project_")))
    location_names = tuple(name for name in names if name == "get_user_location")
    registry = SkillRegistry(tool_registry)
    entries: list[tuple[SkillMetadata, Callable[[], tuple[HealthStatus, str]] | None]] = [
        (SkillMetadata("core", "Core", "1.0", "Core local diagnostic capabilities", "core", True, True, "builtin", tuple(name for name in names if name not in memory_names + reminder_names + ssh_names + location_names), capabilities=("local_diagnostics",), permissions=("read_local_status",)), lambda: _check_core(tool_registry)),
        (SkillMetadata("memory", "Memory", "1.0", "Owner-scoped persistent project memory", "memory", config.memory_enabled, False, "production", memory_names, capabilities=("persistent_memory",), permissions=("read_memory", "write_memory")), lambda: _check_memory(memory_manager)),
        (SkillMetadata("reminders", "Reminders", "1.0", "Owner-scoped scheduled reminders", "scheduling", config.reminders_enabled, False, "production", reminder_names, capabilities=("scheduled_notifications",), permissions=("manage_reminders",)), lambda: _check_reminders(reminder_service, reminder_scheduler)),
        (SkillMetadata("ssh", "SSH", "1.0", "Allowlisted read-only infrastructure monitoring", "infrastructure", config.ssh_enabled, False, "production", ssh_names, capabilities=("remote_monitoring",), permissions=("ssh_read_only",)), lambda: _check_ssh(ssh_dependencies)),
        (SkillMetadata("web_search", "Web Search", "1.0", "Provider-hosted web search capability", "search", config.web_search_enabled, False, "production", (), capabilities=("web_search",), permissions=("web_search",)), lambda: (HealthStatus.OK, "provider capability enabled") if config.web_search_enabled else (HealthStatus.DISABLED, "disabled")),
        (SkillMetadata("location", "Location", "1.0", "Confirmed owner-scoped location and IANA timezone", "context", config.location_enabled, False, "production", location_names, capabilities=("location_context","timezone"), permissions=("read_own_location","write_own_location")), lambda: _check_location(location_service)),
    ]
    registry.register_many(entries)
    return registry
