"""Conservative evidence-bound shadow experiment ideas."""

def _find_limiter(data):
    if isinstance(data,dict):
        for key in ("frequent_limiters","limiters","blockers"):
            value=data.get(key)
            if isinstance(value,dict) and value:return max(value,key=value.get)
            if isinstance(value,list) and value:return str(value[0])
        for value in data.values():
            found=_find_limiter(value)
            if found:return found
    return None
class CryptoIdeaEngine:
    def generate(self,evidence,period):
        if not isinstance(evidence,dict) or not evidence:return []
        limiter=_find_limiter(evidence);ideas=[]
        if limiter:ideas.append({"observation":f"В данных {period} часто встречается ограничитель {limiter}.","evidence":f"Источник: Strategy Laboratory, limiter={limiter}.","hypothesis":f"Нормализация {limiter} может быть чрезмерно строгой в отдельных режимах.","proposed_shadow_experiment":f"Создать отдельного shadow candidate с альтернативной нормализацией {limiter}; production не менять.","expected_signal":"Больше решений в среднем score band без ухудшения drawdown.","risks":["Переобучение","Рост ложных сигналов"],"minimum_sample":"Не менее 7 дней и 10 закрытых shadow-сделок.","stop_condition":"Остановить при ухудшении max drawdown или data quality.","production_changes":False,"confidence_in_idea":"low"})
        return ideas
def build_codex_prompt(idea):
    if not isinstance(idea,dict) or not idea.get("evidence"):raise ValueError("idea evidence required")
    return f"""Проект: /opt/crypto-bot
Факты: {idea['evidence']}
Цель эксперимента: {idea.get('hypothesis','проверить гипотезу')}.
Изучить: Strategy Laboratory, scored candidate observability, tests и deploy/systemd manifests.
Строгие ограничения: не менять production strategy, candidate strategy, thresholds, sizing, paper state или timers; не запускать deployment, trading cycle или реальные ордера.
Реализовать только новый изолированный shadow candidate: {idea.get('proposed_shadow_experiment')}.
Метрики: score bands, closed trades, fees, return, max drawdown, data quality.
Минимальная выборка: {idea.get('minimum_sample')} Stop condition: {idea.get('stop_condition')}.
Добавить unit/integration tests и read-only runtime checks. Сохранить Git hygiene, не коммитить runtime state/секреты.
Commit message: Add shadow strategy experiment
В итоговом отчёте показать файлы, тесты, метрики, риски и доказательство отсутствия production changes. Не выполнять задачу автоматически."""
