"""Strict model tools with identity supplied only by trusted agent context."""

from datetime import datetime,timezone
from app.tools.base import Tool
from app.ssh_agent.service_models import SSHRequestContext
from .models import public
from .analysis import CryptoAnalysisService

def object_schema(props):return {"type":"object","properties":props,"required":list(props),"additionalProperties":False}
PERIOD={"type":"string","enum":["24h","7d","14d","30d","all"]};ENV={"type":"string","enum":["production","candidate","scored_candidate"]}
class CryptoTool(Tool):
    def __init__(self,service,name,description,properties,action):self.service=service;self._name=name;self._description=description;self._properties=properties;self.action=action
    @property
    def name(self):return self._name
    @property
    def description(self):return self._description
    def parameters(self):return object_schema(self._properties)
    def execute(self,**kw):
        u=kw.pop("trusted_user_id");c=kw.pop("trusted_chat_id");m=kw.pop("trusted_source_message_id",None)
        ctx=SSHRequestContext(u,c,f"crypto-{m or 0}-{self._name}",datetime.now(timezone.utc),m,True)
        return public(self.action(ctx,**kw))

def register_crypto_tools(registry,service):
    definitions=(
      ("get_crypto_overview","Get current PAPER crypto-bot overview.",{},lambda c:service.overview(c)),
      ("get_crypto_position","Get owned fixed-environment position diagnostics.",{"environment":ENV},lambda c,environment:service.position(c,environment)),
      ("get_crypto_latest_decision","Get latest scored decision and evidence.",{},lambda c:service.decision(c)),
      ("get_crypto_score_breakdown","Get latest score breakdown.",{},lambda c:service.decision(c)),
      ("compare_crypto_strategies","Compare production and candidate.",{"period":PERIOD},lambda c,period:service.comparison(c,period)),
      ("get_crypto_confidence","Get candidate confidence review.",{},lambda c:service.confidence(c)),
      ("get_crypto_equity_history","Get bounded historical equity metrics.",{"period":PERIOD,"environment":ENV},lambda c,period,environment:service.equity(c,period,environment)),
      ("get_crypto_runtime_issues","Get interpreted runtime warnings/errors.",{},lambda c:service.issues(c)),
      ("get_crypto_git_status","Get crypto-bot Git status through SSH Agent.",{},lambda c:service.git_status(c)),
      ("suggest_crypto_experiments","Suggest evidence-bound shadow experiments.",{"period":PERIOD},lambda c,period:service.ideas(c,period)),)
    definitions += (("prepare_crypto_codex_prompt","Prepare, but never execute, a Codex shadow-experiment task.",{"period":PERIOD,"idea_index":{"type":"integer","minimum":1,"maximum":3}},lambda c,period,idea_index:service.codex_prompt(service.ideas(c,period)[idea_index-1])),)
    for name,desc,props,action in definitions:registry.register(CryptoTool(service,name,desc,props,action))
