"""Small helpers for safe, local-only skill health evaluation."""

from collections.abc import Callable, Mapping

from app.skills.models import HealthStatus, SkillMetadata, SkillReport


HealthCheck = Callable[[], tuple[HealthStatus, str]]


def evaluate_skill(metadata: SkillMetadata, checks: Mapping[str, HealthCheck], dependency_reports: Mapping[str, SkillReport]) -> SkillReport:
    if not metadata.enabled:
        return SkillReport(metadata, HealthStatus.DISABLED, "disabled")
    dependency_status = tuple((name, dependency_reports[name].health_status) for name in metadata.dependencies if name in dependency_reports)
    missing = [name for name in metadata.dependencies if name not in dependency_reports]
    failed = [name for name, status in dependency_status if status == HealthStatus.ERROR]
    if missing:
        return SkillReport(metadata, HealthStatus.ERROR, "missing dependency: " + ", ".join(missing), dependency_status)
    if failed:
        return SkillReport(metadata, HealthStatus.ERROR, "dependency error: " + ", ".join(failed), dependency_status)
    check = checks.get(metadata.skill_id)
    if check is None:
        return SkillReport(metadata, HealthStatus.OK, "ready", dependency_status)
    try:
        status, message = check()
    except Exception:
        status, message = HealthStatus.ERROR, "health check failed"
    return SkillReport(metadata, status, message, dependency_status)
