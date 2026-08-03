"""Crypto Control Center fixed-operation, parsing, analysis and safety tests."""
from datetime import datetime,timezone
from pathlib import Path
import json
import pytest
from app.crypto_control.operations import *
from app.crypto_control.parser import *
from app.crypto_control.parser import _num
from app.crypto_control.analysis import CryptoAnalysisService
from app.crypto_control.ideas import CryptoIdeaEngine,build_codex_prompt
from app.crypto_control.models import *
from app.crypto_control.service import CryptoRemoteClient,CryptoControlService,CryptoControlError
from app.crypto_control.tools import register_crypto_tools
from app.ssh_agent.models import ServerConfig,ProjectConfig,SSHAgentConfig
from app.ssh_agent.registry import ServerRegistry
from app.ssh_agent.service_models import SSHRequestContext
from app.ssh_agent.transport_models import ExecutionResult
from app.ssh_agent.errors import ErrorCode
from app.tools.registry import ToolRegistry
from app.ai.tool_adapter import ToolAdapter,ToolCallValidationError
from app.config import load_config

@pytest.mark.parametrize("name",list(OPERATIONS))
def test_registry_has_operation(name):assert CryptoOperationRegistry().plan(name,period="7d" if name in {"crypto_strategy_lab","crypto_equity_history","crypto_scored_aggregate"} else None).operation==name
@pytest.mark.parametrize("name",list(OPERATIONS))
def test_fixed_cwd(name):assert CryptoOperationRegistry().plan(name,period="7d" if "strategy" in name or "equity" in name or "aggregate" in name else None).argv[:3]==("/usr/bin/env","-C","/opt/crypto-bot")
@pytest.mark.parametrize("name",list(OPERATIONS))
def test_fixed_python(name):assert CryptoOperationRegistry().plan(name,period="7d" if name in {"crypto_strategy_lab","crypto_equity_history","crypto_scored_aggregate"} else None).argv[3]=="/opt/crypto-bot/venv/bin/python"
@pytest.mark.parametrize("period",sorted(PERIODS))
def test_lab_periods(period):assert period in CryptoOperationRegistry().plan("crypto_strategy_lab",period=period).argv
@pytest.mark.parametrize("period",["1d","8d","7d;id","$(id)","all|cat","","ALL"])
def test_period_rejected(period):
 with pytest.raises(ValueError):CryptoOperationRegistry().plan("crypto_strategy_lab",period=period)
@pytest.mark.parametrize("environment",sorted(ENVIRONMENTS))
def test_environment_allowlist(environment):assert CryptoOperationRegistry().plan("crypto_equity_history",environment=environment if environment!="scored_candidate" else None).argv
@pytest.mark.parametrize("host",["1.2.3.4","crypto;id","crypto host","$(id)","/tmp/x","crypto.example.com",""])
def test_host_alias_rejected(host):
 with pytest.raises(ValueError):CryptoOperationRegistry(host)
@pytest.mark.parametrize("fragment",["sudo","/bin/sh","bash","|",">","<","git pull","systemctl restart","run_bybit_controller","backfill_equity_history"])
def test_no_write_or_shell_fragment(fragment):assert all(fragment not in " ".join(CryptoOperationRegistry().plan(n,period="7d" if n in {"crypto_strategy_lab","crypto_equity_history","crypto_scored_aggregate"} else None).argv) for n in OPERATIONS)

@pytest.mark.parametrize(("value","expected"),[(None,None),("N/A",None),("",None),("1.5",1.5),(2,2.0),("bad",None)])
def test_numeric_tolerance(value,expected):assert _num(value)==expected
@pytest.mark.parametrize("bad",["", "[]", "null", "oops", "1", '"x"'])
def test_invalid_json_or_schema(bad):
 with pytest.raises(CryptoParseError):decode(bad)
def test_output_too_large():
 with pytest.raises(CryptoParseError):decode(json.dumps({"x":"a"*100}),10)
def test_decision_parser_full():
 d=CryptoResultParser().decision({"decision":"HOLD","total_score":34.37,"entry_threshold":65,"strong_entry_threshold":80,"distance_to_entry":30.63,"risk_allocation_pct":0,"score_components":{"Trend":10},"limiting_components":["Trend"]});assert d.score==34.37 and d.limiters==["Trend"]
def test_decision_parser_legacy():assert CryptoResultParser().decision({"score_breakdown":None}).score is None
def test_confidence_parser():
 c=CryptoResultParser().confidence({"confidence":23,"level":"LOW","recommendation":"INSUFFICIENT_DATA","blockers":["days"]});assert c.confidence==23 and c.automatic_promotion is False
def test_comparison_parser():assert CryptoResultParser().comparison({"production":{"return":1},"candidate":{"return":2}},"7d").period=="7d"
def test_equity_parser():
 e=CryptoResultParser().equity({"environments":{"production":{"rolling":{"start_equity":100,"end_equity":110,"return_percent":10,"closed_trades":2},"quality":{"gap_count":0}}}},"7d","production");assert e.pnl==10 and e.trades==2
def test_runtime_checks_only_dicts():assert len(CryptoResultParser().runtime({"checks":[{},"x"]})["checks"])==1

@pytest.mark.parametrize(("score","threshold","phrase"),[(34.37,65,"30.63"),(65,65,"0.00"),(70,65,"0.00")])
def test_hold_explanation(score,threshold,phrase):
 d=DecisionStatus(decision="HOLD",score=score,entry_threshold=threshold,allocation_pct=0,limiters=["Trend"]);assert phrase in CryptoAnalysisService().explain_hold(d)
def test_missing_breakdown():assert "недоступен" in CryptoAnalysisService().explain_hold(DecisionStatus())
def test_non_hold():assert "BUY" in CryptoAnalysisService().explain_hold(DecisionStatus(decision="BUY"))
@pytest.mark.parametrize(("status","severity"),[("WARNING","warning"),("CRITICAL","critical"),("FAIL","critical"),("ERROR","critical")])
def test_runtime_issue_severity(status,severity):assert CryptoAnalysisService().runtime_issues({"checks":[{"status":status,"name":"api","message":"x"}]})[0].severity==severity
def test_ok_not_issue():assert CryptoAnalysisService().runtime_issues({"checks":[{"status":"OK","name":"x"}]})==[]
def test_stale_market_issue():assert any(x.code=="STALE_MARKET" for x in CryptoAnalysisService().runtime_issues({"local_state_lagging_market_data":True}))

def evidence():return {"frequent_limiters":{"Trend":84},"closed_trades":0,"fees":1}
def test_idea_evidence_required():assert CryptoIdeaEngine().generate({},"7d")==[]
def test_idea_from_limiter():assert "Trend" in CryptoIdeaEngine().generate(evidence(),"7d")[0]["evidence"]
def test_idea_no_production_change():assert CryptoIdeaEngine().generate(evidence(),"7d")[0]["production_changes"] is False
def test_idea_minimum_sample():assert CryptoIdeaEngine().generate(evidence(),"7d")[0]["minimum_sample"]
def test_idea_stop_condition():assert CryptoIdeaEngine().generate(evidence(),"7d")[0]["stop_condition"]
def test_idea_low_confidence():assert CryptoIdeaEngine().generate(evidence(),"7d")[0]["confidence_in_idea"]=="low"
@pytest.mark.parametrize("word",["Факты","shadow candidate","не менять production","tests","Git hygiene","не запускать deployment","реальные ордера","Commit message","итоговом отчёте"])
def test_codex_prompt_guards(word):assert word.lower() in build_codex_prompt(CryptoIdeaEngine().generate(evidence(),"7d")[0]).lower()
def test_codex_requires_evidence():
 with pytest.raises(ValueError):build_codex_prompt({})

def server_registry():
 p=ProjectConfig("crypto-bot",Path("/opt/crypto-bot"),("crypto-paper.timer",));s=ServerConfig("crypto","host",22,"monitor",Path("/key"),"crypto",True,{"crypto-bot":p});return ServerRegistry(SSHAgentConfig(1,{"crypto":s}))
def context(allowed=True):return SSHRequestContext(1,2,"test",datetime.now(timezone.utc),3,allowed)
class FakeTransport:
 def __init__(self,payload=None,success=True,error=None):self.payload=payload or {};self.success=success;self.error=error;self.calls=0
 async def __call__(self,server,plan):
  self.calls+=1;return ExecutionResult(plan.operation,server.alias,self.success,0 if self.success else 1,json.dumps(self.payload),"",1,False,False,self.error,len(json.dumps(self.payload)))
@pytest.mark.asyncio
async def test_remote_fetch_and_cache():
 t=FakeTransport({"ok":1});r=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=t);assert await r.fetch(context(),"crypto_runtime_health")=={"ok":1};await r.fetch(context(),"crypto_runtime_health");assert t.calls==1
@pytest.mark.asyncio
async def test_remote_context_required():
 r=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=FakeTransport())
 with pytest.raises(CryptoControlError):await r.fetch(context(False),"crypto_runtime_health")
@pytest.mark.asyncio
async def test_remote_invalid_json():
 class Bad(FakeTransport):
  async def __call__(self,s,p):return ExecutionResult(p.operation,s.alias,True,0,"bad","",1,False,False,None,3)
 r=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=Bad())
 with pytest.raises(CryptoControlError) as e:await r.fetch(context(),"crypto_runtime_health")
 assert e.value.code=="invalid_json"
@pytest.mark.asyncio
async def test_secretlike_output_blocked():
 r=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=FakeTransport({"value":"[REDACTED]"}))
 with pytest.raises(CryptoControlError) as e:await r.fetch(context(),"crypto_runtime_health")
 assert e.value.code=="SECRET_REDACTED" and r.last_status=="warning"
@pytest.mark.asyncio
async def test_error_short_cache():
 t=FakeTransport(success=False,error=ErrorCode.SSH_COMMAND_TIMEOUT);r=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=t)
 for _ in range(2):
  with pytest.raises(CryptoControlError):await r.fetch(context(),"crypto_runtime_health")
 assert t.calls==1

@pytest.mark.asyncio
async def test_read_allowed():
 assert await CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=FakeTransport({"status":"ok"})).fetch(context(),"crypto_runtime_health")=={"status":"ok"}

@pytest.mark.asyncio
async def test_read_denied_has_actionable_message():
 remote=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=FakeTransport(success=False,error=ErrorCode.SSH_REMOTE_PERMISSION_DENIED))
 with pytest.raises(CryptoControlError) as caught:await remote.fetch(context(),"crypto_runtime_health")
 assert caught.value.code=="SSH_REMOTE_PERMISSION_DENIED"
 assert caught.value.user_message=="Runtime недоступен.\n\nПричина:\nREAD_PERMISSION_DENIED\n\nНеобходимо предоставить monitor-пользователю доступ только на чтение."

@pytest.mark.asyncio
async def test_partial_acl_is_reported_per_operation():
 allowed=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=FakeTransport({"status":"ok"}))
 denied=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=FakeTransport(success=False,error=ErrorCode.SSH_REMOTE_PERMISSION_DENIED))
 assert await allowed.fetch(context(),"crypto_runtime_health")=={"status":"ok"}
 with pytest.raises(CryptoControlError) as caught:await denied.fetch(context(),"crypto_strategy_lab",period="24h")
 assert caught.value.code=="SSH_REMOTE_PERMISSION_DENIED"

@pytest.mark.asyncio
async def test_missing_db_is_remote_command_failure():
 remote=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=FakeTransport(success=False,error=ErrorCode.SSH_REMOTE_COMMAND_FAILED))
 with pytest.raises(CryptoControlError) as caught:await remote.fetch(context(),"crypto_equity_history",period="all",environment="production")
 assert caught.value.code=="SSH_REMOTE_COMMAND_FAILED"

@pytest.mark.asyncio
async def test_timeout_is_stable():
 remote=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=FakeTransport(success=False,error=ErrorCode.SSH_COMMAND_TIMEOUT))
 with pytest.raises(CryptoControlError) as caught:await remote.fetch(context(),"crypto_runtime_health")
 assert caught.value.code=="SSH_COMMAND_TIMEOUT"

@pytest.mark.asyncio
@pytest.mark.parametrize(("operation","payload","kwargs"),[
 ("crypto_runtime_health",{"checks":[]},{}),
 ("crypto_strategy_lab",{"confidence":80},{"period":"24h"}),
 ("crypto_equity_history",{"environments":{"production":{}}},{"period":"all","environment":"production"}),
 ("crypto_latest_decision",{"decision":"HOLD"},{}),
])
async def test_successful_read_only_operation(operation,payload,kwargs):
 remote=CryptoRemoteClient(server_registry(),CryptoOperationRegistry(),transport=FakeTransport(payload))
 assert await remote.fetch(context(),operation,**kwargs)==payload

def test_tool_schemas_hide_trusted_fields():
 reg=ToolRegistry();register_crypto_tools(reg,object())
 for schema in ToolAdapter(reg).schemas():assert not ({"host","path","command","executable","user_id","chat_id"}&set(schema["parameters"]["properties"]))
def test_tools_registered():
 reg=ToolRegistry();register_crypto_tools(reg,object());assert len(reg.list_tools())==11
def test_arbitrary_tool_argument_rejected():
 reg=ToolRegistry();register_crypto_tools(reg,object());a=ToolAdapter(reg)
 with pytest.raises(ToolCallValidationError):a.parse_and_validate("get_crypto_overview",'{"command":"id"}')
def test_invalid_period_tool_rejected():
 reg=ToolRegistry();register_crypto_tools(reg,object());a=ToolAdapter(reg)
 with pytest.raises(ToolCallValidationError):a.parse_and_validate("compare_crypto_strategies",'{"period":"1d"}')

BASE={"TELEGRAM_BOT_TOKEN":"x","OPENAI_API_KEY":"x","TELEGRAM_ALLOWED_USER_IDS":"1"}
def test_feature_disabled_default():assert load_config(BASE).crypto_control_enabled is False
def test_feature_config():
 c=load_config({**BASE,"CRYPTO_CONTROL_ENABLED":"true","CRYPTO_CONTROL_HOST":"crypto","CRYPTO_CONTROL_CACHE_SECONDS":"30"});assert c.crypto_control_enabled and c.crypto_control_host=="crypto"
@pytest.mark.parametrize("host",["1.2.3.4","host.name","bad host","/path","x;id"])
def test_config_rejects_non_alias_host(host):
 with pytest.raises(RuntimeError):load_config({**BASE,"CRYPTO_CONTROL_HOST":host})
@pytest.mark.parametrize("periods",["1d","7d,7d","","all,evil"])
def test_config_rejects_periods(periods):
 with pytest.raises(RuntimeError):load_config({**BASE,"CRYPTO_CONTROL_ALLOWED_PERIODS":periods})
