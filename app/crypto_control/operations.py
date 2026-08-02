"""Exact crypto CLI operation catalog and fixed argv builder."""

from dataclasses import dataclass
from types import MappingProxyType
from app.ssh_agent.execution_plan import ExecutionPlan

ROOT="/opt/crypto-bot";PYTHON=ROOT+"/venv/bin/python";ENV="/usr/bin/env"
PERIODS=frozenset({"24h","7d","14d","30d","all"});ENVIRONMENTS=frozenset({"production","candidate","scored_candidate"})
@dataclass(frozen=True,slots=True)
class CryptoOperation:
    name:str;script:str;timeout:int=20;max_bytes:int=1_048_576

OPERATIONS=MappingProxyType({x.name:x for x in (
 CryptoOperation("crypto_runtime_health","scripts/runtime_status.py"),CryptoOperation("crypto_strategy_lab","scripts/show_strategy_lab.py"),
 CryptoOperation("crypto_equity_history","scripts/show_equity_history.py"),CryptoOperation("crypto_latest_decision","scripts/show_scored_candidate.py"),
 CryptoOperation("crypto_scored_aggregate","scripts/show_scored_candidate.py"),)})

class CryptoOperationRegistry:
    def __init__(self,host_alias="crypto",timeout=20,max_output_bytes=1_048_576):
        if not host_alias or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in host_alias):raise ValueError("invalid host alias")
        self.host_alias=host_alias;self.timeout=min(max(1,timeout),30);self.max_output=min(max(1024,max_output_bytes),1_048_576)
    def names(self):return tuple(OPERATIONS)
    def plan(self,name,*,period=None,environment=None):
        if name not in OPERATIONS:raise ValueError("operation not allowed")
        if period is not None and period not in PERIODS:raise ValueError("period not allowed")
        if environment is not None and environment not in ENVIRONMENTS:raise ValueError("environment not allowed")
        op=OPERATIONS[name];argv=[ENV,"-C",ROOT,PYTHON,ROOT+"/"+op.script]
        if name=="crypto_runtime_health":argv += ["--json","--no-network"]
        elif name=="crypto_strategy_lab":argv += ["--period",period or "24h","--json"]
        elif name=="crypto_equity_history":
            argv += ["--window",period or "all"]
            if environment in {"production","candidate"}:argv += ["--environment",environment]
            argv += ["--json"]
        elif name=="crypto_latest_decision":argv += ["--latest","--components","--json"]
        else:
            aggregate=period or "24h"
            if aggregate not in {"24h","7d","all"}:raise ValueError("score aggregate period not supported")
            argv += ["--aggregate",aggregate,"--json"]
        return ExecutionPlan(name,self.host_alias,tuple(argv),self.timeout,self.max_output,32768,1000,True,{"crypto":True,"period":period or "","environment":environment or ""})
