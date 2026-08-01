#!/usr/bin/env python3
"""Dry-run or transactionally initialize controlled family access."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.access import AccessStorage  # noqa: E402
from app.config import load_config  # noqa: E402
from scripts.production_rollout import RolloutPaths, _values  # noqa: E402


def integrity(path: Path) -> bool:
    if not path.exists():
        return True
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def memory_report(path: Path) -> dict[str, int]:
    result = {"personal_candidates": 0, "technical_candidates": 0,
              "family_auto_migrations": 0}
    if not path.exists():
        return result
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        result["technical_candidates"] = db.execute(
            "SELECT count(*) FROM memories WHERE owner_id=0 OR scope IN ('environment','system','project')"
        ).fetchone()[0]
        result["personal_candidates"] = db.execute(
            "SELECT count(*) FROM memories WHERE owner_id<>0 AND scope NOT IN ('environment','system','project')"
        ).fetchone()[0]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.dry_run == args.apply:
        parser.error("choose exactly one of --dry-run or --apply")
    config = load_config(_values(RolloutPaths().env_file))
    report = memory_report(config.memory_db_path)
    print("memory migration report:", report)
    print("policy: no existing memory is reclassified; family sharing is explicit only")
    if args.dry_run:
        print("family access migration: DRY RUN")
        return 0
    if not integrity(config.access_db_path):
        print("access database integrity check failed")
        return 1
    if config.access_db_path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = config.access_db_path.with_suffix(f".db.backup-{stamp}")
        shutil.copy2(config.access_db_path, backup)
        print(f"backup: {backup}")
    storage = AccessStorage(config.access_db_path)
    storage.initialize(config.telegram_allowed_user_ids)
    try:
        shutil.chown(config.access_db_path, user="jarvis", group="jarvis")
        config.access_db_path.chmod(0o600)
    except LookupError:
        pass
    if not storage.validate_schema() or not integrity(config.access_db_path):
        print("family access migration validation failed")
        return 1
    print("family access migration: APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
