from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _compose_config() -> dict[str, object]:
    completed = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        env={**os.environ, "DOCKER_GID": str(os.stat("/var/run/docker.sock").st_gid)},
        text=True,
    )
    parsed = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    return parsed


def test_image_has_one_foreground_web_process() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    command_lines = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]
    assert command_lines == ['CMD ["node", "packages/ts/app/dist/server.js"]'], (
        "the image default must be one exec-form foreground web process"
    )
    assert " & " not in dockerfile
    assert 'CMD ["sh", "-c"' not in dockerfile


def test_compose_separates_migration_web_and_worker_roles() -> None:
    config = _compose_config()
    services = config.get("services")
    assert isinstance(services, dict)
    assert {"postgres", "jaeger", "migrate", "web", "worker"} <= services.keys()

    migrate = services["migrate"]
    web = services["web"]
    worker = services["worker"]
    assert isinstance(migrate, dict) and isinstance(web, dict) and isinstance(worker, dict)
    assert migrate.get("restart") == "no"
    assert migrate.get("command") == [
        "python",
        "-m",
        "enten.migrations",
        "apply",
        "--baseline-existing",
    ]
    assert web.get("command") == ["node", "packages/ts/app/dist/server.js"]
    assert worker.get("command") == ["enten", "worker"]


def test_compose_waits_for_database_and_migration_readiness() -> None:
    config = _compose_config()
    services = config["services"]
    assert isinstance(services, dict)
    for role in ("web", "worker"):
        service = services[role]
        assert isinstance(service, dict)
        dependencies = service.get("depends_on")
        assert isinstance(dependencies, dict)
        assert dependencies["postgres"]["condition"] == "service_healthy"
        assert dependencies["migrate"]["condition"] == "service_completed_successfully"


def test_worker_has_only_required_host_capabilities() -> None:
    config = _compose_config()
    services = config["services"]
    assert isinstance(services, dict)
    worker = services["worker"]
    assert isinstance(worker, dict)
    assert str(os.stat("/var/run/docker.sock").st_gid) in worker["group_add"]
    volumes = worker["volumes"]
    assert isinstance(volumes, list)
    targets = {volume["target"]: volume for volume in volumes}
    assert targets["/var/run/docker.sock"]["type"] == "bind"
    assert targets["/run/secrets/github-app.pem"]["read_only"] is True
    environment = worker["environment"]
    assert environment["GITHUB_APP_PRIVATE_KEY_PATH"] == "/run/secrets/github-app.pem"


def test_compose_is_project_isolated_and_loopback_bound() -> None:
    raw = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = raw["services"]
    assert all("container_name" not in service for service in services.values())
    for service_name in ("postgres", "jaeger"):
        for published_port in services[service_name].get("ports", []):
            assert str(published_port).startswith("127.0.0.1:")


def test_dev_overlay_does_not_disable_auth_or_mount_repository_root() -> None:
    overlay = yaml.safe_load((ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    serialized = json.dumps(overlay, sort_keys=True)
    assert "POSTGRES_HOST_AUTH_METHOD" not in serialized
    assert '"./:/app' not in serialized


def test_dev_scripts_start_full_stack_and_preserve_data_by_default() -> None:
    up_script = (ROOT / "scripts" / "dev-up.sh").read_text(encoding="utf-8")
    down_script = (ROOT / "scripts" / "dev-down.sh").read_text(encoding="utf-8")
    assert "SERVICES=(postgres jaeger migrate web worker)" in up_script
    assert "GITHUB_APP_PRIVATE_KEY_PATH" in up_script
    assert "GITHUB_WEBHOOK_SECRET" in up_script
    assert "DOCKER_GID" in up_script
    assert "--drop-data" in down_script
    assert 'VOLUME_FLAG=""' in down_script


def test_platform_configs_keep_web_worker_and_migration_separate() -> None:
    fly = tomllib.loads((ROOT / "fly.toml").read_text(encoding="utf-8"))
    railway_web = tomllib.loads((ROOT / "railway.toml").read_text(encoding="utf-8"))
    railway_worker = tomllib.loads((ROOT / "railway.worker.toml").read_text(encoding="utf-8"))
    assert fly["processes"] == {
        "web": "node packages/ts/app/dist/server.js",
        "worker": "enten worker",
    }
    migration = "python -m enten.migrations apply --baseline-existing"
    assert fly["deploy"]["release_command"] == migration
    assert railway_web["deploy"]["preDeployCommand"] == migration
    assert railway_web["deploy"]["startCommand"] == "node packages/ts/app/dist/server.js"
    assert railway_worker["deploy"]["startCommand"] == "enten worker"
