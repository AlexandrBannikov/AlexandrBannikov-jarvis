"""Render reviewed deployment artifacts from fixed templates only."""

from __future__ import annotations

import json
from pathlib import Path
import re

from app.ssh_agent.config import load_config

from .manifest import build_manifest, manifest_json
from .models import DeploymentInventory, ServerInventory

_SCRIPT_HEADER = """#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\\n\\t'
umask 077
# GENERATED PLAN: REVIEW BEFORE USE. This file is never executed by Jarvis.
"""
_MUTATING_GUARD = """
MODE=""
RESTART_SERVICE=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    --apply) MODE="apply" ;;
    --restart-service) RESTART_SERVICE=true ;;
    *) printf 'Unknown argument: %s\\n' "$arg" >&2; exit 2 ;;
  esac
done
if [[ -z "$MODE" ]]; then
  printf 'No changes made. Use --dry-run, then --apply after review.\\n'
  exit 2
fi
run() {
  if [[ "$MODE" == "dry-run" ]]; then
    printf 'PLAN:'
    printf ' %q' "$@"
    printf '\\n'
  else
    "$@"
  fi
}
"""


class RenderError(ValueError):
    code = "SSH_DEPLOYMENT_RENDER_INVALID"


def _safe_output(path: Path) -> Path:
    resolved = path.resolve()
    forbidden = {Path("/"), Path("/etc"), Path("/usr"), Path("/var"), Path("/opt")}
    if resolved in forbidden or any(root in resolved.parents for root in forbidden - {Path("/")}):
        raise RenderError
    if path.exists() and path.is_symlink():
        raise RenderError
    return resolved


def generated_config(inventory: DeploymentInventory) -> dict[str, object]:
    local = inventory.local
    servers: dict[str, object] = {}
    for server in sorted(inventory.servers, key=lambda item: item.alias):
        projects = {
            project.alias: {
                "path": str(project.remote_path),
                "services": list(project.allowed_services),
            }
            for project in sorted(server.projects, key=lambda item: item.alias)
        }
        servers[server.alias] = {
            "host": server.host_placeholder,
            "port": server.port,
            "user": server.remote_user,
            "identity_file": str(local.ssh_config_dir / server.identity_name),
            "host_key_alias": server.alias,
            "enabled": server.enabled,
            "projects": projects,
        }
    return {"version": 1, "servers": servers}


def _local_prepare(inventory: DeploymentInventory) -> str:
    local = inventory.local
    return _SCRIPT_HEADER + _MUTATING_GUARD + f"""
if ! getent passwd '{local.service_user}' >/dev/null; then
  printf 'Required service user is missing.\\n' >&2; exit 1
fi
if ! getent group '{local.service_group}' >/dev/null; then
  printf 'Required service group is missing.\\n' >&2; exit 1
fi
run install -d -m 0700 -o '{local.service_user}' -g '{local.service_group}' '{local.jarvis_config_dir}'
run install -d -m 0700 -o '{local.service_user}' -g '{local.service_group}' '{local.ssh_config_dir}'
"""


def _key_script(inventory: DeploymentInventory) -> str:
    identities = " ".join(f"'{item.identity_name}'" for item in inventory.servers)
    return _SCRIPT_HEADER + """
MODE=""
KEY_MODE=""
CONFIRMED=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    --apply) MODE="apply" ;;
    --interactive-passphrase) KEY_MODE="interactive" ;;
    --no-passphrase) KEY_MODE="noninteractive" ;;
    --confirm-noninteractive) CONFIRMED=true ;;
    *) printf 'Unknown argument: %s\\n' "$arg" >&2; exit 2 ;;
  esac
done
if [[ -z "$MODE" ]]; then
  printf 'No keys generated. First choose passphrase policy and --dry-run.\\n'
  exit 2
fi
if [[ -z "$KEY_MODE" ]]; then
  printf 'Choose --interactive-passphrase or --no-passphrase explicitly.\\n'
  exit 2
fi
if [[ "$KEY_MODE" == "noninteractive" && "$CONFIRMED" != true ]]; then
  printf 'Non-interactive key requires --confirm-noninteractive.\\n' >&2; exit 2
fi
KEY_DIR="$(pwd)/operator-generated-keys"
if [[ "$MODE" == "apply" ]]; then install -d -m 0700 "$KEY_DIR"; fi
""" + f"""
IDENTITIES=({identities})
for name in "${{IDENTITIES[@]}}"; do
  target="$KEY_DIR/$name"
  if [[ -e "$target" || -e "$target.pub" ]]; then
    printf 'Refusing to overwrite existing key: %s\\n' "$name" >&2; exit 1
  fi
  if [[ "$MODE" == "dry-run" ]]; then
    printf 'PLAN: generate dedicated Ed25519 key %s (private content will not be printed)\\n' "$name"
  elif [[ "$KEY_MODE" == "interactive" ]]; then
    ssh-keygen -t ed25519 -f "$target" -C "jarvis-ops:$name"
  else
    ssh-keygen -t ed25519 -N '' -f "$target" -C "jarvis-ops:$name"
  fi
done
"""


def _remote_create(server: ServerInventory) -> str:
    paths = " ".join(f"'{project.remote_path}'" for project in server.projects)
    return _SCRIPT_HEADER + _MUTATING_GUARD + f"""
if [[ "$(id -u)" -ne 0 ]]; then printf 'Run locally on reviewed remote host as root.\\n' >&2; exit 1; fi
if [[ '{server.remote_user}' == root ]]; then printf 'Root remote user is forbidden.\\n' >&2; exit 1; fi
if ! id '{server.remote_user}' >/dev/null 2>&1; then
  run useradd --create-home --shell /bin/bash '{server.remote_user}'
fi
run passwd --lock '{server.remote_user}'
run install -d -m 0700 -o '{server.remote_user}' -g '{server.remote_user}' '/home/{server.remote_user}/.ssh'
PROJECT_PATHS=({paths})
for path in "${{PROJECT_PATHS[@]}}"; do
  printf 'REVIEW ACL: grant only read/traverse access to %s; never change project ownership.\\n' "$path"
done
printf 'No sudo, deployment, restart, source ownership or broad groups are granted.\\n'
printf 'Journal access, if approved, must be reviewed separately; systemd-journal exposes broad logs.\\n'
"""


def _install_public_key(server: ServerInventory) -> str:
    restrictions = "restrict,no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty,no-user-rc"
    return _SCRIPT_HEADER + _MUTATING_GUARD + f"""
if [[ "$(id -u)" -ne 0 ]]; then printf 'Run locally on reviewed remote host as root.\\n' >&2; exit 1; fi
PUBLIC_KEY_FILE="./{server.identity_name}.pub"
if [[ ! -f "$PUBLIC_KEY_FILE" || -L "$PUBLIC_KEY_FILE" ]]; then
  printf 'Reviewed public key file is missing or unsafe.\\n' >&2; exit 1
fi
if [[ "$(wc -l < "$PUBLIC_KEY_FILE")" -ne 1 ]]; then
  printf 'Public key must contain one record.\\n' >&2; exit 1
fi
TARGET='/home/{server.remote_user}/.ssh/authorized_keys'
if [[ "$MODE" == "dry-run" ]]; then
  printf 'PLAN: install one restricted public key for {server.remote_user}; key content not printed.\\n'
else
  install -d -m 0700 -o '{server.remote_user}' -g '{server.remote_user}' '/home/{server.remote_user}/.ssh'
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  printf '%s %s\\n' '{restrictions}' "$(cat "$PUBLIC_KEY_FILE")" >"$tmp"
  install -m 0600 -o '{server.remote_user}' -g '{server.remote_user}' "$tmp" "$TARGET"
fi
"""


def _known_hosts(inventory: DeploymentInventory) -> str:
    lines = [
        "HOST-KEY TRUST IS MANUAL. ssh-keyscan output alone is not trusted proof of identity.",
        "For each alias: obtain the key through a trusted console/provider channel,",
        "compare its fingerprint independently, then install the exact approved line.",
        "StrictHostKeyChecking must remain enabled; never overwrite a changed key automatically.",
        "",
    ]
    for server in inventory.servers:
        lines.extend((
            f"[{server.alias}] candidate retrieval (operator-run only):",
            f"  ssh-keyscan -p {server.port} -- {server.host_placeholder} > candidate-{server.alias}.known_hosts",
            f"  ssh-keygen -lf candidate-{server.alias}.known_hosts",
            "  STOP until the independently supplied fingerprint matches.",
            "",
        ))
    return "\n".join(lines)


def _install_config(inventory: DeploymentInventory) -> str:
    local = inventory.local
    return _SCRIPT_HEADER + _MUTATING_GUARD + f"""
SOURCE_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
CONFIG_SOURCE="$SOURCE_DIR/servers.generated.json"
KNOWN_SOURCE="$SOURCE_DIR/known_hosts.approved"
[[ -f "$CONFIG_SOURCE" && ! -L "$CONFIG_SOURCE" ]] || {{ printf 'Generated config missing.\\n' >&2; exit 1; }}
[[ -f "$KNOWN_SOURCE" && ! -L "$KNOWN_SOURCE" ]] || {{ printf 'Operator-approved known_hosts missing.\\n' >&2; exit 1; }}
for identity in "$SOURCE_DIR"/operator-generated-keys/*_ed25519; do
  [[ -f "$identity" && ! -L "$identity" ]] || {{ printf 'Reviewed key files are missing.\\n' >&2; exit 1; }}
  [[ "$(stat -c '%a' "$identity")" == 600 ]] || {{ printf 'Private key mode must be 600.\\n' >&2; exit 1; }}
done
run install -d -m 0700 -o '{local.service_user}' -g '{local.service_group}' '{local.ssh_config_dir}'
if [[ "$MODE" == "dry-run" ]]; then
  printf 'PLAN: atomically install reviewed config and known_hosts; preserve prior config backup.\\n'
else
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  if [[ -f '{local.servers_config_path}' ]]; then
    cp --preserve=mode,ownership '{local.servers_config_path}' '{local.servers_config_path}.backup.'"$stamp"
  fi
  config_tmp="$(mktemp '{local.jarvis_config_dir}/.servers.json.XXXXXX')"
  hosts_tmp="$(mktemp '{local.ssh_config_dir}/.known_hosts.XXXXXX')"
  trap 'rm -f "$config_tmp" "$hosts_tmp"' EXIT
  install -m 0600 -o '{local.service_user}' -g '{local.service_group}' "$CONFIG_SOURCE" "$config_tmp"
  install -m 0600 -o '{local.service_user}' -g '{local.service_group}' "$KNOWN_SOURCE" "$hosts_tmp"
  mv -f "$config_tmp" '{local.servers_config_path}'
  mv -f "$hosts_tmp" '{local.known_hosts_path}'
fi
for identity in "$SOURCE_DIR"/operator-generated-keys/*_ed25519; do
  run install -m 0600 -o '{local.service_user}' -g '{local.service_group}' "$identity" '{local.ssh_config_dir}/'
done
printf 'SSH remains disabled. Run offline readiness validation before enablement.\\n'
"""


def _validate_local(inventory: DeploymentInventory) -> str:
    local = inventory.local
    return _SCRIPT_HEADER + f"""
printf 'Offline validation only; no SSH connection is made.\\n'
python -m app.ssh_agent.cli --config '{local.servers_config_path}' validate-config
JARVIS_SSH_ENABLED=true JARVIS_SERVERS_CONFIG='{local.servers_config_path}' python -m app.ssh_agent.cli readiness
JARVIS_SSH_ENABLED=true JARVIS_SERVERS_CONFIG='{local.servers_config_path}' python -m app.ssh_agent.cli health
"""


def _remote_verify(server: ServerInventory) -> str:
    checks = []
    for project in server.projects:
        checks.extend((
            f"test -r '{project.remote_path}'",
            f"test ! -w '{project.remote_path}'",
            f"test ! -r '{project.remote_path}/.env'",
        ))
        for service in project.allowed_services:
            checks.extend((
                f"systemctl show --no-pager --property=ActiveState,SubState -- '{service}'",
                f"journalctl --no-pager --lines 5 --unit '{service}'",
            ))
    return _SCRIPT_HEADER + f"""
[[ "$(id -u)" -ne 0 ]] || {{ printf 'Verification must run as non-root.\\n' >&2; exit 1; }}
id
if sudo -n true 2>/dev/null; then printf 'STOP: unrestricted sudo appears available.\\n' >&2; exit 1; fi
""" + "\n".join(checks) + """
printf 'Also verify password authentication is unavailable and unrelated service access is rejected.\\n'
"""


def _enable(inventory: DeploymentInventory) -> str:
    local = inventory.local
    return _SCRIPT_HEADER + _MUTATING_GUARD + f"""
ENV_FILE='{local.environment_file_path}'
JARVIS_SSH_ENABLED=true JARVIS_SERVERS_CONFIG='{local.servers_config_path}' python -m app.ssh_agent.cli --config '{local.servers_config_path}' validate-runtime
if [[ "$MODE" == "apply" ]]; then
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {{ printf 'Environment file missing or unsafe.\\n' >&2; exit 1; }}
  backup="$ENV_FILE.backup.$(date -u +%Y%m%dT%H%M%SZ)"
  cp --preserve=mode,ownership "$ENV_FILE" "$backup"
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  grep -Ev '^(JARVIS_SSH_ENABLED|JARVIS_SERVERS_CONFIG)=' "$ENV_FILE" >"$tmp"
  printf 'JARVIS_SSH_ENABLED=true\\nJARVIS_SERVERS_CONFIG=%s\\n' '{local.servers_config_path}' >>"$tmp"
  install -m 0600 "$tmp" "$ENV_FILE"
  printf 'Rollback backup created; use 99-rollback.sh --apply after review.\\n'
fi
if [[ "$RESTART_SERVICE" == true ]]; then run systemctl restart jarvis.service; fi
"""


def _smoke() -> str:
    return _SCRIPT_HEADER + """
printf '%s\\n' \
  '1. Confirm readiness code SSH_READY.' \
  '2. Ask Jarvis to list configured servers and projects.' \
  '3. Request server summary, project status and last commit.' \
  '4. Request approved service status and bounded recent logs.' \
  '5. Confirm secret-like output is redacted.' \
  '6. Confirm shell/write/restart requests are rejected.' \
  '7. Confirm reminders, Web Search and Project Memory still work independently.' \
  '8. Confirm SSH health metrics update.'
python -m app.ssh_agent.cli health
"""


def _rollback(inventory: DeploymentInventory) -> str:
    local = inventory.local
    return _SCRIPT_HEADER + _MUTATING_GUARD + f"""
ENV_FILE='{local.environment_file_path}'
if [[ "$MODE" == "apply" ]]; then
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {{ printf 'Environment file missing or unsafe.\\n' >&2; exit 1; }}
  backup="$ENV_FILE.backup.$(date -u +%Y%m%dT%H%M%SZ)"
  cp --preserve=mode,ownership "$ENV_FILE" "$backup"
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  grep -Ev '^JARVIS_SSH_ENABLED=' "$ENV_FILE" >"$tmp"
  printf 'JARVIS_SSH_ENABLED=false\\n' >>"$tmp"
  install -m 0600 "$tmp" "$ENV_FILE"
fi
if [[ "$RESTART_SERVICE" == true ]]; then run systemctl restart jarvis.service; fi
printf 'Keys are NOT deleted. Revoke remote public keys and lock/remove account separately if required.\\n'
printf 'Reminders, Web Search and Project Memory are preserved. Verify SSH_DISABLED in health.\\n'
"""


def _checklist() -> str:
    return """# SSH deployment checklist

## Codex completed
- [x] Offline code and trusted templates prepared.
- [x] Inventory and generated configuration validated.
- [x] Scripts rendered for review; no deployment performed.

## Operator required
- [ ] Confirm real aliases, addresses, remote users, paths and services.
- [ ] Generate or approve one dedicated key per server.
- [ ] Verify every host fingerprint independently.
- [ ] Copy and execute reviewed remote scripts manually.
- [ ] Install reviewed local runtime files.
- [ ] Enable SSH explicitly and run smoke tests.
- [ ] Paste command output back for review.

## Stop conditions
Stop immediately if a fingerprint differs, root SSH is proposed, the project is
writable by the restricted user, key permissions are unsafe, inventory has
unknown fields, readiness is not SSH_READY, tests regress, sensitive SSH
configuration is exposed, or any arbitrary shell/write tool appears.
"""


def render_kit(inventory: DeploymentInventory, output: Path) -> tuple[Path, ...]:
    target = _safe_output(output)
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise RenderError("output directory must be empty")
    manifest = build_manifest(inventory)
    files: dict[str, str] = {
        "00-README.txt": (
            "OFFLINE DEPLOYMENT KIT. Review every file. Nothing here was executed.\n"
            "Remote scripts must be copied and run manually on the confirmed host.\n"
        ),
        "10-local-prepare.sh": _local_prepare(inventory),
        "20-generate-keys.sh": _key_script(inventory),
        "50-local-known-hosts-instructions.txt": _known_hosts(inventory),
        "60-local-install-config.sh": _install_config(inventory),
        "70-validate-local.sh": _validate_local(inventory),
        "90-enable-ssh-agent.sh": _enable(inventory),
        "95-smoke-test.sh": _smoke(),
        "99-rollback.sh": _rollback(inventory),
        "servers.generated.json": json.dumps(
            generated_config(inventory), ensure_ascii=False,
            sort_keys=True, indent=2,
        ) + "\n",
        "deployment-manifest.json": manifest_json(manifest),
        "CHECKLIST.md": _checklist(),
    }
    for server in inventory.servers:
        files[f"30-remote-create-user-{server.alias}.sh"] = _remote_create(server)
        files[f"40-remote-install-public-key-{server.alias}.sh"] = _install_public_key(server)
        files[f"80-verify-remote-{server.alias}.sh"] = _remote_verify(server)
    written = []
    for name in sorted(files):
        path = target / name
        path.write_text(files[name], encoding="utf-8")
        path.chmod(0o700 if name.endswith(".sh") else 0o600)
        written.append(path)
    verify_rendered(target)
    return tuple(written)


def verify_rendered(output: Path) -> None:
    target = _safe_output(output)
    manifest_path = target / "deployment-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RenderError from error
    expected = set(manifest.get("artifact_names", []))
    actual = {path.name for path in target.iterdir() if path.is_file()}
    if expected != actual:
        raise RenderError("rendered artifact set mismatch")
    forbidden = (
        "curl |", "wget |", "StrictHostKeyChecking=no",
        "PasswordAuthentication=yes", "PermitRootLogin yes",
    )
    for path in target.iterdir():
        if not path.is_file():
            raise RenderError
        text = path.read_text(encoding="utf-8")
        if re.search(r"(^|\s)eval\s", text) or any(item in text for item in forbidden):
            raise RenderError("unsafe rendered content")
        if path.suffix == ".sh":
            if not text.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail"):
                raise RenderError("unsafe shell header")
    config_path = target / "servers.generated.json"
    load_config(config_path, validate_permissions=False)
