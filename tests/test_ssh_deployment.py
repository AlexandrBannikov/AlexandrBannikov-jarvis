import json
from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys

import pytest

from app.ssh_agent.config import load_config
from app.ssh_agent.deployment.inventory import (
    InventoryError, load_inventory, parse_inventory,
)
from app.ssh_agent.deployment.manifest import (
    build_manifest, manifest_json,
)
from app.ssh_agent.deployment.renderer import (
    RenderError, render_kit, verify_rendered,
)


EXAMPLE = Path("config/ssh-deployment.example.json")


def raw() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_inventory_valid_disabled_and_placeholder_only() -> None:
    inventory = load_inventory(EXAMPLE)
    assert len(inventory.servers) == 1
    assert not inventory.servers[0].enabled
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "SERVER_ADDRESS_PLACEHOLDER" in text
    assert "127.0.0.1" not in text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value["servers"][0].update({"remote_user": "root"}),
        lambda value: value["servers"][0].update({"password": "bad"}),
        lambda value: value["servers"][0].update({"command": "id"}),
        lambda value: value["servers"][0].update({"alias": "bad;id"}),
        lambda value: value["local"].update({"ssh_config_dir": "relative"}),
        lambda value: value["servers"][0]["projects"][0].update(
            {"allowed_services": ["a.service", "a.service"]}
        ),
        lambda value: value["servers"].append(dict(value["servers"][0])),
        lambda value: value["servers"][0].update(
            {"display_name": "private_key=not-allowed"}
        ),
        lambda value: value["servers"][0].update({"ssh_options": {}}),
    ],
)
def test_inventory_rejects_unknown_secret_duplicate_and_unsafe_values(mutate) -> None:
    value = raw()
    mutate(value)
    with pytest.raises(InventoryError):
        parse_inventory(value)


def test_inventory_count_bounds() -> None:
    value = raw()
    value["servers"] = [dict(value["servers"][0], alias=f"s{index}",
                             identity_name=f"s{index}_ed25519")
                        for index in range(65)]
    with pytest.raises(InventoryError):
        parse_inventory(value)


def test_manifest_is_immutable_deterministic_and_secret_free() -> None:
    manifest = build_manifest(load_inventory(EXAMPLE))
    first = manifest_json(manifest)
    second = manifest_json(build_manifest(load_inventory(EXAMPLE)))
    assert first == second
    assert "private_key" not in first and "password" not in first
    assert "JARVIS_SSH_ENABLED" in first
    assert "example_ops_ed25519" in first
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        manifest.version = 2
    with pytest.raises(TypeError):
        manifest.expected_environment["bad"] = "value"


def test_renderer_is_deterministic_complete_and_runtime_config_valid(tmp_path: Path) -> None:
    inventory = load_inventory(EXAMPLE)
    first = tmp_path / "first"
    second = tmp_path / "second"
    paths = render_kit(inventory, first)
    render_kit(inventory, second)
    names = {path.name for path in paths}
    expected = {
        "00-README.txt", "10-local-prepare.sh", "20-generate-keys.sh",
        "30-remote-create-user-example.sh",
        "40-remote-install-public-key-example.sh",
        "50-local-known-hosts-instructions.txt",
        "60-local-install-config.sh", "70-validate-local.sh",
        "80-verify-remote-example.sh", "90-enable-ssh-agent.sh",
        "95-smoke-test.sh", "99-rollback.sh",
        "servers.generated.json", "deployment-manifest.json", "CHECKLIST.md",
    }
    assert names == expected
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    config = load_config(first / "servers.generated.json",
                         validate_permissions=False)
    assert not config.servers["example"].enabled
    verify_rendered(first)


def test_rendered_shell_safety_and_explicit_mutation_flags(tmp_path: Path) -> None:
    output = tmp_path / "kit"
    render_kit(load_inventory(EXAMPLE), output)
    all_text = "\n".join(
        path.read_text(encoding="utf-8") for path in output.iterdir()
    )
    for forbidden in (
        "curl |", "wget |", "StrictHostKeyChecking=no",
        "PasswordAuthentication=yes", "PermitRootLogin yes",
        "useradd root", "NOPASSWD: ALL",
    ):
        assert forbidden not in all_text
    assert "\neval " not in all_text
    scripts = list(output.glob("*.sh"))
    assert scripts
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash\nset -Eeuo pipefail")
        assert "GENERATED PLAN" in text
    mutating = [
        "10-local-prepare.sh", "20-generate-keys.sh",
        "30-remote-create-user-example.sh",
        "40-remote-install-public-key-example.sh",
        "60-local-install-config.sh", "90-enable-ssh-agent.sh",
        "99-rollback.sh",
    ]
    for name in mutating:
        text = (output / name).read_text(encoding="utf-8")
        assert "--dry-run" in text and "--apply" in text
    assert "ssh-keygen" in (output / "20-generate-keys.sh").read_text()
    rollback = (output / "99-rollback.sh").read_text()
    assert "JARVIS_SSH_ENABLED=false" in rollback
    assert "Keys are NOT deleted" in rollback
    enable = (output / "90-enable-ssh-agent.sh").read_text()
    assert "--restart-service" in enable
    assert "systemctl restart" in enable


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.ssh_agent.deployment.cli", *args],
        check=False, capture_output=True, text=True,
    )


def test_offline_cli_validate_plan_render_inspect_verify(tmp_path: Path) -> None:
    assert run_cli("validate-inventory", str(EXAMPLE)).returncode == 0
    plan = run_cli("plan", str(EXAMPLE))
    assert plan.returncode == 0
    assert json.loads(plan.stdout)["version"] == 1
    output = tmp_path / "kit"
    rendered = run_cli("render", str(EXAMPLE), "--output", str(output))
    assert rendered.returncode == 0
    assert "Ничего не выполнено" in rendered.stdout
    assert run_cli(
        "inspect-manifest", str(output / "deployment-manifest.json")
    ).returncode == 0
    assert run_cli("verify-rendered", str(output)).returncode == 0


def test_cli_rejects_invalid_inventory_and_unsafe_output(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    result = run_cli("validate-inventory", str(invalid))
    assert result.returncode != 0
    assert "SSH_DEPLOYMENT_INVENTORY_INVALID" in result.stdout
    with pytest.raises(RenderError):
        render_kit(load_inventory(EXAMPLE), Path("/etc"))


def test_deployment_package_has_no_transport_network_or_execution_imports() -> None:
    root = Path("app/ssh_agent/deployment")
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in root.glob("*.py")
    )
    assert "app.ssh_agent.transport" not in source
    assert "import socket" not in source
    assert "import subprocess" not in source
    assert "os.system" not in source
    assert "subprocess.run" not in source


def test_checklist_and_repository_hygiene(tmp_path: Path) -> None:
    output = tmp_path / "kit"
    render_kit(load_inventory(EXAMPLE), output)
    checklist = (output / "CHECKLIST.md").read_text(encoding="utf-8")
    for heading in ("Codex completed", "Operator required", "Stop conditions"):
        assert heading in checklist
    ignored = Path(".gitignore").read_text(encoding="utf-8")
    assert "/build/ssh-deployment/" in ignored
    assert "*.key" in ignored and "*.pem" in ignored
