"""Evidence-only explanations of decisions and runtime health."""

from .models import RuntimeIssue
class CryptoAnalysisService:
    def explain_hold(self,d):
        if not d.decision:return "Для этой свечи подробный breakdown недоступен."
        if d.decision.upper()!="HOLD":return f"Последнее решение: {d.decision}."
        if d.score is None or d.entry_threshold is None:return "Решение HOLD подтверждено, но подробный breakdown недоступен."
        distance=d.distance_to_entry if d.distance_to_entry is not None else max(0,d.entry_threshold-d.score)
        limiters=", ".join(d.limiters) if d.limiters else "не указаны"
        return f"Бот не вошёл: score {d.score:.2f} ниже порога {d.entry_threshold:.2f} на {distance:.2f}. Allocation: {(d.allocation_pct or 0):.1f}%. Ограничители: {limiters}."
    def runtime_issues(self,runtime):
        issues=[]
        for check in runtime.get("checks",[]):
            status=str(check.get("status","UNKNOWN")).upper()
            if status in {"OK","PASS"}:continue
            name=str(check.get("name","runtime"));message=str(check.get("message","Проверка не пройдена"))[:500]
            severity="critical" if status in {"CRITICAL","FAIL","ERROR"} else "warning"
            issues.append(RuntimeIssue(severity,name.upper(),name,message,None,"Повторить соответствующую read-only диагностику."))
        if runtime.get("local_state_lagging_market_data"):issues.append(RuntimeIssue("warning","STALE_MARKET","market_data","Локальная свеча отстаёт от рынка.",None,"Проверить timestamp последней свечи и timer status."))
        return issues
