"""Trusted SSH orchestration, bounded cache and public crypto service."""

import asyncio
from dataclasses import asdict
from datetime import datetime,timezone
import threading,time
from app.ssh_agent.service_models import SSHRequestContext
from app.ssh_agent.transport import execute
from .operations import CryptoOperationRegistry,PERIODS,ENVIRONMENTS
from .parser import CryptoParseError,CryptoResultParser,decode
from .models import CryptoOverview,PositionStatus,public

class CryptoControlError(RuntimeError):
    def __init__(self,code,message):super().__init__(message);self.code=code;self.user_message=message

READ_PERMISSION_MESSAGE="Runtime недоступен.\n\nПричина:\nREAD_PERMISSION_DENIED\n\nНеобходимо предоставить monitor-пользователю доступ только на чтение."
ERROR_MESSAGES={"SSH_CONNECTION_TIMEOUT":"SSH-соединение с crypto-сервером недоступно.","SSH_CONNECTION_REFUSED":"SSH-соединение с crypto-сервером недоступно.","SSH_AUTHENTICATION_FAILED":"SSH-соединение с crypto-сервером недоступно.","SSH_COMMAND_TIMEOUT":"Диагностический CLI превысил допустимое время выполнения.","SSH_REMOTE_COMMAND_FAILED":"Не удалось получить состояние crypto-bot.","SSH_REMOTE_PERMISSION_DENIED":READ_PERMISSION_MESSAGE,"SSH_OUTPUT_TRUNCATED":"Ответ диагностического CLI слишком большой.","invalid_json":"Диагностический CLI вернул повреждённый JSON.","output_too_large":"Ответ диагностического CLI слишком большой.","permission_denied":READ_PERMISSION_MESSAGE}

class CryptoRemoteClient:
    def __init__(self,registry,operations,*,ssh_service=None,cache_seconds=30,error_cache_seconds=5,transport=execute):
        self.registry=registry;self.operations=operations;self.ssh_service=ssh_service;self.cache_seconds=cache_seconds;self.error_cache_seconds=error_cache_seconds;self.transport=transport;self._cache={};self._lock=threading.Lock();self.last_check_at=None;self.last_status="never";self.last_error_code=None
    def cache_entries(self):
        now=time.monotonic()
        with self._lock:return sum(exp>now for exp,_,_ in self._cache.values())
    async def fetch(self,context,name,*,period=None,environment=None):
        if type(context) is not SSHRequestContext or not context.is_allowlisted or context.user_id<=0 or context.chat_id==0:raise CryptoControlError("SSH_CONTEXT_INVALID","Некорректный trusted context.")
        key=(name,period,environment);now=time.monotonic()
        with self._lock:cached=self._cache.get(key)
        if cached and cached[0]>now:
            if cached[2]:raise cached[2]
            return cached[1]
        try:
            server=self.registry.get_server(self.operations.host_alias);plan=self.operations.plan(name,period=period,environment=environment)
            raw=await self.transport(server,plan)
            if not raw.success:
                code=raw.error_code.value if raw.error_code else "SSH_PROCESS_ERROR"
                if code=="SSH_REMOTE_COMMAND_FAILED" and "permission" in raw.stderr_safe.lower():code="permission_denied"
                raise CryptoControlError(code,ERROR_MESSAGES.get(code,"Не удалось получить состояние crypto-bot."))
            if "[REDACTED]" in raw.stdout:
                raise CryptoControlError("SECRET_REDACTED","Диагностический вывод содержал секретоподобные данные и был заблокирован.")
            value=decode(raw.stdout,self.operations.max_output)
            self.last_check_at=datetime.now(timezone.utc).isoformat();self.last_status="ok";self.last_error_code=None
            with self._lock:self._cache[key]=(now+self.cache_seconds,value,None)
            return value
        except CryptoParseError as e:
            error=CryptoControlError(str(e),ERROR_MESSAGES.get(str(e),"Диагностический CLI вернул несовместимую схему."));self._failed(key,now,error);raise error
        except CryptoControlError as error:self._failed(key,now,error);raise
    def _failed(self,key,now,error):
        self.last_check_at=datetime.now(timezone.utc).isoformat();self.last_status="warning" if error.code=="SECRET_REDACTED" else "error";self.last_error_code=error.code
        with self._lock:self._cache[key]=(now+self.error_cache_seconds,None,error)
    async def git_status(self,context):
        if self.ssh_service is None:raise CryptoControlError("SSH_UNAVAILABLE","Не удалось получить состояние crypto-bot.")
        result=await self.ssh_service.get_project_status(context,self.operations.host_alias,"crypto-bot")
        if not result.success:raise CryptoControlError(result.error_code.value if result.error_code else "SSH_PROCESS_ERROR",result.message or "Не удалось получить состояние crypto-bot.")
        return dict(result.data)

class CryptoControlService:
    def __init__(self,remote,*,ideas_enabled=True,codex_prompts_enabled=True):self.remote=remote;self.parser=CryptoResultParser();self.ideas_enabled=ideas_enabled;self.codex_prompts_enabled=codex_prompts_enabled
    def _run(self,coro):return asyncio.run(coro)
    def runtime(self,context):return self.parser.runtime(self._run(self.remote.fetch(context,"crypto_runtime_health")))
    def decision(self,context):return self.parser.decision(self._run(self.remote.fetch(context,"crypto_latest_decision")))
    def lab(self,context,period="24h"):self._period(period);return self._run(self.remote.fetch(context,"crypto_strategy_lab",period=period))
    def equity(self,context,period="all",environment="production"):
        self._period(period);self._environment(environment)
        if environment=="scored_candidate":raise CryptoControlError("INVALID_ENVIRONMENT","Equity history доступна только для production/candidate.")
        return self.parser.equity(self._run(self.remote.fetch(context,"crypto_equity_history",period=period,environment=environment)),period,environment)
    def confidence(self,context,period="7d"):return self.parser.confidence(self.lab(context,period))
    def comparison(self,context,period="7d"):return self.parser.comparison(self.lab(context,period),period)
    def position(self,context,environment="production"):
        self._environment(environment);runtime=self.runtime(context);equity=None
        if environment in {"production","candidate"}:
            try:equity=self.equity(context,"24h",environment)
            except CryptoControlError:pass
        side=runtime.get("current_position") if environment=="production" else None
        return PositionStatus(environment,side,_float(runtime.get("open_position_quantity")),_float(runtime.get("open_position_entry_price")),None,_float(runtime.get("virtual_balance")),equity.end_equity if equity else None,None,None,equity.pnl if equity else None,equity.return_pct if equity else None,equity.fees if equity else None,equity.current_drawdown if equity else None,runtime.get("last_processed_candle_timestamp"))
    def overview(self,context):
        runtime=self.runtime(context);decision=self.decision(context)
        try:confidence=self.confidence(context)
        except CryptoControlError:confidence=None
        position=self.position(context,"production")
        checks=runtime.get("checks",[]);halt=next((x.get("message") for x in checks if x.get("name")=="operational_state" and x.get("status")!="OK"),None)
        return CryptoOverview("PAPER",runtime.get("symbol"),str(runtime.get("timeframe")) if runtime.get("timeframe") is not None else None,None,runtime.get("last_processed_candle_timestamp"),1 if runtime.get("local_state_lagging_market_data") else 0,runtime.get("overall_status"),halt,public(position),public(confidence) if confidence else {},public(decision),runtime.get("current_utc_time"))
    def issues(self,context):
        from .analysis import CryptoAnalysisService
        return CryptoAnalysisService().runtime_issues(self.runtime(context))
    def ideas(self,context,period="7d"):
        if not self.ideas_enabled:raise CryptoControlError("IDEAS_DISABLED","Идеи экспериментов отключены.")
        from .ideas import CryptoIdeaEngine
        return CryptoIdeaEngine().generate(self.lab(context,period),period)
    def codex_prompt(self,idea):
        if not self.codex_prompts_enabled:raise CryptoControlError("CODEX_PROMPTS_DISABLED","Подготовка заданий Codex отключена.")
        from .ideas import build_codex_prompt
        return build_codex_prompt(idea)
    def git_status(self,context):return self._run(self.remote.git_status(context))
    @staticmethod
    def _period(v):
        if v not in PERIODS:raise CryptoControlError("INVALID_PERIOD","Недопустимый период.")
    @staticmethod
    def _environment(v):
        if v not in ENVIRONMENTS:raise CryptoControlError("INVALID_ENVIRONMENT","Недопустимое окружение.")
def _float(v):
    try:return float(v) if v is not None else None
    except (ValueError,TypeError):return None
