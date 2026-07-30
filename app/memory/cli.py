"""Read-only memory diagnostics plus explicit bootstrap."""

from __future__ import annotations
import argparse
import json
from pathlib import Path

from app.memory.formatter import safe_json, safe_line
from app.memory.service import MemoryService
from app.memory.storage import MemoryStorage


def parser() -> argparse.ArgumentParser:
    root=argparse.ArgumentParser(prog="python -m app.memory.cli")
    root.add_argument("--db",type=Path,default=Path("/var/lib/jarvis/memory.db"))
    root.add_argument("--json",action="store_true")
    commands=root.add_subparsers(dest="command",required=True)
    for name in ("status","list"):
        item=commands.add_parser(name); item.add_argument("--owner-id",type=int,default=0)
    project=commands.add_parser("project"); project.add_argument("project_key")
    project.add_argument("--owner-id",type=int,default=0)
    context=commands.add_parser("context"); context.add_argument("--owner-id",type=int,required=True)
    context.add_argument("--query",default="")
    bootstrap=commands.add_parser("bootstrap"); bootstrap.add_argument("file",type=Path)
    bootstrap.add_argument("--owner-id",type=int,default=0)
    return root


def _bootstrap(service: MemoryService, path: Path, owner_id: int) -> dict[str,int]:
    data=json.loads(path.read_text(encoding="utf-8"))
    memories=projects=0
    for item in data.get("projects",[]):
        key=item["project_key"]; fields={k:str(v) for k,v in item.items()
                                        if k not in {"project_key","facts"}}
        service.update_project(owner_id,key,**fields); projects+=1
        for key_name,value in item.get("facts",{}).items():
            service.remember(owner_id=owner_id,scope="project",namespace=key,key=key_name,
                             value=value,summary=f"{key_name.replace('_',' ').title()}: {value}",
                             source="bootstrap",importance=8,confidence=1)
            memories+=1
    for item in data.get("memories",[]):
        service.remember(owner_id=owner_id,source="bootstrap",confidence=1,
                         **item); memories+=1
    return {"projects":projects,"memories":memories}


def main(argv: list[str] | None=None) -> int:
    args=parser().parse_args(argv)
    storage=MemoryStorage(args.db,read_only=args.command!="bootstrap")
    # Read-only commands must not create or migrate a database.
    if args.command!="bootstrap" and (not args.db.exists()):
        raise SystemExit(f"Memory database does not exist: {args.db}")
    if args.command!="bootstrap" and not storage.validate_schema():
        raise SystemExit("Memory database schema is not ready")
    service=MemoryService(storage) if args.command=="bootstrap" else object.__new__(MemoryService)
    if args.command!="bootstrap":
        service.storage=storage
        from app.memory.context_builder import MemoryContextBuilder
        service.context_builder=MemoryContextBuilder()
    if args.command=="bootstrap":
        result=_bootstrap(service,args.file,args.owner_id)
    elif args.command=="status":
        result={"enabled":True,"active_memories":storage.count_active(args.owner_id),
                "projects":len(storage.list_projects(args.owner_id)),
                "schema_version":2}
    elif args.command=="list":
        result=[m.public_dict() for m in storage.list_active(
            owner_id=args.owner_id,include_system=True)[:50]]
    elif args.command=="project":
        result=service.get_project_context(args.owner_id,args.project_key)
    else:
        result=service.build_user_context(args.owner_id,args.query)
    if args.json: print(safe_json(result))
    elif isinstance(result,str): print(result)
    elif isinstance(result,list):
        for item in result: print(safe_line(f"#{item['id']} {item['summary']}"))
    else:
        for key,value in result.items(): print(f"{key}: {safe_line(value)}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
