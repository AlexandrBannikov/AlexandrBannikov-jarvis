"""Deterministic safe deployment manifest construction."""

from __future__ import annotations

from dataclasses import asdict
import json

from .models import DeploymentInventory, DeploymentManifest


def artifact_names(inventory: DeploymentInventory) -> tuple[str, ...]:
    names = [
        "00-README.txt", "10-local-prepare.sh", "20-generate-keys.sh",
        "50-local-known-hosts-instructions.txt", "60-local-install-config.sh",
        "70-validate-local.sh", "90-enable-ssh-agent.sh", "95-smoke-test.sh",
        "99-rollback.sh", "servers.generated.json",
        "deployment-manifest.json", "CHECKLIST.md",
    ]
    for server in inventory.servers:
        names.extend((
            f"30-remote-create-user-{server.alias}.sh",
            f"40-remote-install-public-key-{server.alias}.sh",
            f"80-verify-remote-{server.alias}.sh",
        ))
    return tuple(sorted(names))


def build_manifest(inventory: DeploymentInventory) -> DeploymentManifest:
    local = inventory.local
    local_steps = (
        {"kind": "directory", "target": "jarvis_config_dir",
         "owner": local.service_user, "group": local.service_group, "mode": "0700"},
        {"kind": "directory", "target": "ssh_config_dir",
         "owner": local.service_user, "group": local.service_group, "mode": "0700"},
        {"kind": "install_reviewed_config", "mode": "0600"},
        {"kind": "install_preexisting_keys", "mode": "0600"},
        {"kind": "verify_known_hosts_manually", "mode": "0600"},
        {"kind": "operator_restart_optional", "service": "jarvis.service"},
    )
    server_steps = tuple(
        {
            "alias": server.alias,
            "remote_user": server.remote_user,
            "identity_name": server.identity_name,
            "host_key_verification": "independent_manual_confirmation_required",
            "project_paths": tuple(str(project.remote_path) for project in server.projects),
            "allowed_services": tuple(
                service for project in server.projects
                for service in project.allowed_services
            ),
            "mutations_require_apply": True,
        }
        for server in inventory.servers
    )
    return DeploymentManifest(
        1, local_steps, server_steps,
        {
            "JARVIS_SSH_ENABLED": "false",
            "JARVIS_SERVERS_CONFIG": str(local.servers_config_path),
        },
        artifact_names(inventory),
    )


def manifest_dict(manifest: DeploymentManifest) -> dict[str, object]:
    return {
        "version": manifest.version,
        "local_steps": [dict(item) for item in manifest.local_steps],
        "server_steps": [dict(item) for item in manifest.server_steps],
        "expected_environment": dict(manifest.expected_environment),
        "artifact_names": list(manifest.artifact_names),
    }


def manifest_json(manifest: DeploymentManifest) -> str:
    return json.dumps(manifest_dict(manifest), ensure_ascii=False,
                      sort_keys=True, indent=2) + "\n"
