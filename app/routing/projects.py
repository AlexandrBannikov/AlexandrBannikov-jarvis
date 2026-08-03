"""Safe project ownership registry exposed only as aliases/capabilities."""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ProjectPolicy:
    alias: str
    owner_project: bool = False
    external_project: bool = False
    access: str = "diagnostics_only"
    remediation: str = "report_only"


PROJECTS = MappingProxyType({
    item.alias: item for item in (
        ProjectPolicy("jarvis", owner_project=True, access="local_diagnostics", remediation="report_only"),
        ProjectPolicy("crypto-bot", external_project=True, access="read_only", remediation="report_or_codex_prompt"),
        ProjectPolicy("fin-vpn-bot", external_project=True, access="read_only", remediation="report_or_codex_prompt"),
    )
})

EXTERNAL_WRITE_MESSAGE = (
    "Источник проблемы: {project}.\n\n"
    "Jarvis может подготовить отдельное задание для Codex в проекте {project_path}, "
    "но не может применить исправление сам."
)


def safe_registry() -> dict[str, dict[str, object]]:
    return {alias: {
        "owner_project": item.owner_project,
        "external_project": item.external_project,
        "access": item.access,
        "remediation": item.remediation,
    } for alias, item in PROJECTS.items()}
