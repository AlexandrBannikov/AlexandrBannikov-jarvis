"""Deterministic registry describing built-in skills without executing tools."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import logging

from app.skills.health import HealthCheck, evaluate_skill
from app.skills.models import HealthStatus, SkillMetadata, SkillReport
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class SkillRegistry:
    def __init__(self, tool_registry: ToolRegistry) -> None:
        self.tool_registry = tool_registry
        self._skills: dict[str, SkillMetadata] = {}
        self._checks: dict[str, HealthCheck] = {}
        self._reports: dict[str, SkillReport] = {}
        self._tool_owners: dict[str, str] = {}

    def register(self, metadata: SkillMetadata, health_check: HealthCheck | None = None) -> None:
        self._validate_metadata(metadata)
        if metadata.skill_id in self._skills:
            raise ValueError(f"Skill already registered: {metadata.skill_id}")
        available = {tool.name for tool in self.tool_registry.list_tools()}
        for tool_name in metadata.tool_names:
            if tool_name not in available:
                raise ValueError(f"Unknown tool for skill {metadata.skill_id}: {tool_name}")
            owner = self._tool_owners.get(tool_name)
            if owner is not None:
                raise ValueError(f"Tool already owned by skill {owner}: {tool_name}")
        self._skills[metadata.skill_id] = metadata
        if health_check is not None:
            self._checks[metadata.skill_id] = health_check
        for tool_name in metadata.tool_names:
            self._tool_owners[tool_name] = metadata.skill_id

    def register_many(self, entries: Iterable[tuple[SkillMetadata, HealthCheck | None]]) -> None:
        for metadata, check in entries:
            self.register(metadata, check)

    def get(self, skill_id: str) -> SkillMetadata:
        try:
            return self._skills[skill_id]
        except KeyError as error:
            raise KeyError(f"Unknown skill: {skill_id}") from error

    def list(self) -> list[SkillMetadata]:
        return [self._skills[name] for name in sorted(self._skills)]

    def enable(self, skill_id: str) -> SkillMetadata:
        metadata = self.get(skill_id)
        if metadata.enabled:
            return metadata
        replacement = SkillMetadata(skill_id=metadata.skill_id, name=metadata.name, version=metadata.version, description=metadata.description, category=metadata.category, enabled=True, required=metadata.required, source=metadata.source, tool_names=metadata.tool_names, dependencies=metadata.dependencies, capabilities=metadata.capabilities, permissions=metadata.permissions)
        self._skills[skill_id] = replacement
        return replacement

    def disable(self, skill_id: str) -> SkillMetadata:
        metadata = self.get(skill_id)
        if not metadata.enabled:
            return metadata
        replacement = SkillMetadata(skill_id=metadata.skill_id, name=metadata.name, version=metadata.version, description=metadata.description, category=metadata.category, enabled=False, required=metadata.required, source=metadata.source, tool_names=metadata.tool_names, dependencies=metadata.dependencies, capabilities=metadata.capabilities, permissions=metadata.permissions)
        self._skills[skill_id] = replacement
        return replacement

    def tools_for_skill(self, skill_id: str) -> tuple[str, ...]:
        return self.get(skill_id).tool_names

    def health(self) -> list[SkillReport]:
        reports: dict[str, SkillReport] = {}
        for metadata in self.list():
            reports[metadata.skill_id] = evaluate_skill(metadata, self._checks, reports)
        self._reports = reports
        return [reports[name] for name in sorted(reports)]

    def summary(self) -> dict[str, int]:
        reports = self.health()
        counts = {status.value: 0 for status in HealthStatus}
        for report in reports:
            counts[report.health_status.value] += 1
        return {"total": len(reports), "ok": counts["ok"], "warning": counts["warning"], "error": counts["error"], "disabled": counts["disabled"]}

    def required_errors(self) -> list[SkillReport]:
        return [report for report in self.health() if report.metadata.required and report.health_status == HealthStatus.ERROR]

    def _validate_metadata(self, metadata: SkillMetadata) -> None:
        if not metadata.skill_id or metadata.source not in {"builtin", "production"}:
            raise ValueError("Invalid skill metadata")
        if metadata.skill_id != metadata.skill_id.strip() or any(char in metadata.skill_id for char in "/\\"):
            raise ValueError("Invalid skill id")
        if len(set(metadata.tool_names)) != len(metadata.tool_names):
            raise ValueError("Duplicate tool name in skill")
