"""Read-only operator CLI for Crypto Control Center."""

import argparse,json,os
from app.config import load_config
from app.ssh_agent.bootstrap import build_ssh_dependencies
from .operations import CryptoOperationRegistry,PERIODS,ENVIRONMENTS
from .service import CryptoControlService,CryptoRemoteClient,CryptoControlError
from .models import public
from .tools import *
from app.ssh_agent.service_models import SSHRequestContext
from datetime import datetime,timezone
def parser():
 p=argparse.ArgumentParser();p.add_argument("--json",action="store_true");s=p.add_subparsers(dest="command",required=True)
 for x in ("overview","decision","score","confidence","issues","validate"):s.add_parser(x)
 x=s.add_parser("position");x.add_argument("--environment",choices=ENVIRONMENTS,default="production")
 x=s.add_parser("comparison");x.add_argument("--period",choices=PERIODS,default="7d")
 x=s.add_parser("equity");x.add_argument("--period",choices=PERIODS,default="30d");x.add_argument("--environment",choices=("production","candidate"),default="production")
 x=s.add_parser("ideas");x.add_argument("--period",choices=PERIODS,default="7d");return p
def main(argv=None):
 a=parser().parse_args(argv);c=load_config();d=build_ssh_dependencies(enabled=c.ssh_enabled,config_path=c.ssh_servers_config_path)
 ops=CryptoOperationRegistry(c.crypto_control_host,c.crypto_control_timeout_seconds,c.crypto_control_max_output_bytes);remote=CryptoRemoteClient(d.registry,ops,ssh_service=d.service,cache_seconds=c.crypto_control_cache_seconds);service=CryptoControlService(remote,ideas_enabled=c.crypto_control_ideas_enabled,codex_prompts_enabled=c.crypto_control_codex_prompts_enabled)
 if a.command=="validate":value={"enabled":c.crypto_control_enabled,"host_configured":any(x.alias==c.crypto_control_host for x in d.registry.list_servers()),"operations_registered":len(ops.names()),"ssh_ready":d.readiness.ready}
 else:
  owner=min(c.telegram_allowed_user_ids) if c.telegram_allowed_user_ids else 1;ctx=SSHRequestContext(owner,owner,"crypto-cli",datetime.now(timezone.utc),None,True)
  try:value={"overview":service.overview,"position":lambda x:service.position(x,a.environment),"decision":service.decision,"score":service.decision,"comparison":lambda x:service.comparison(x,a.period),"confidence":service.confidence,"equity":lambda x:service.equity(x,a.period,a.environment),"issues":service.issues,"ideas":lambda x:service.ideas(x,a.period)}[a.command](ctx)
  except CryptoControlError as e:value={"error":e.code,"message":e.user_message}
 print(json.dumps(public(value),ensure_ascii=False,indent=2) if a.json else public(value));return 0
if __name__=="__main__":raise SystemExit(main())
