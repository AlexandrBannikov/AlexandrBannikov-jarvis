"""Privacy-preserving document administration CLI."""

import argparse
import json
import os
from pathlib import Path
from .storage import DocumentSessionStorage

def _storage():
    return DocumentSessionStorage(Path(os.getenv("DOCUMENTS_DB_PATH","/var/lib/jarvis/document_sessions.db")),Path(os.getenv("DOCUMENTS_STORAGE_PATH","/var/lib/jarvis/documents")))
def main(argv=None):
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("status");p=sub.add_parser("list");p.add_argument("--user-id",type=int,required=True)
    p=sub.add_parser("show");p.add_argument("--id",required=True);p.add_argument("--user-id",type=int,required=True)
    sub.add_parser("validate");p=sub.add_parser("cleanup");p.add_argument("--dry-run",action="store_true");p.add_argument("--apply",action="store_true")
    args=parser.parse_args(argv);storage=_storage()
    if args.command=="validate": print(json.dumps({"schema_ok":storage.validate_schema()}));return
    if args.command=="status": print(json.dumps(storage.metrics()));return
    if args.command=="list": print(json.dumps([{"id":x.id,"filename":x.safe_filename,"type":x.document_type,"expires_at":x.expires_at.isoformat()} for x in storage.list(args.user_id)],ensure_ascii=False));return
    if args.command=="show":
        x=storage.get(args.id,args.user_id);print(json.dumps(None if not x else {"id":x.id,"filename":x.safe_filename,"type":x.document_type,"size":x.file_size,"status":x.status,"expires_at":x.expires_at.isoformat()},ensure_ascii=False));return
    if args.command=="cleanup":
        if not args.dry_run and not args.apply:parser.error("cleanup requires --dry-run or --apply")
        print(json.dumps({"candidates":storage.cleanup(args.apply),"applied":args.apply}))
if __name__=="__main__":main()
