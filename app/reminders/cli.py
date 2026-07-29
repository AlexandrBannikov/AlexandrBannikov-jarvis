"""Read-only reminder diagnostics."""

import argparse
import os

from app.config import load_config
from app.reminders.storage import ReminderStorage, utc_now


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status", "list", "due", "validate", "show"])
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--id", type=int)
    parser.add_argument("--show-content", action="store_true")
    args = parser.parse_args()
    config = load_config(os.environ)
    storage = ReminderStorage(config.reminders_db_path)
    if args.command == "validate":
        print("valid" if storage.validate_schema() else "invalid")
        return 0 if storage.validate_schema() else 1
    if args.command == "status":
        metrics = storage.metrics()
        print(f"active={metrics['active']} due={metrics['due']} failed={metrics['failed']}")
        return 0
    if args.command == "due":
        print(f"due={storage.metrics(utc_now())['due']}")
        return 0
    if args.command == "list":
        if not args.user_id:
            parser.error("--user-id is required")
        for item in storage.list_user(args.user_id):
            print(f"id={item.id} status={item.status} type={item.reminder_type} next={item.next_run_at_utc}")
        return 0
    if not args.id or not args.user_id:
        parser.error("--id and --user-id are required")
    item = storage.get(args.id, args.user_id)
    if item is None:
        print("not found")
        return 1
    print(f"id={item.id} status={item.status} type={item.reminder_type} next={item.next_run_at_utc}")
    if args.show_content:
        print("WARNING: reminder content is sensitive")
        print(item.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
