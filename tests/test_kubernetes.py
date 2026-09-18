import json
import subprocess

import pytest

from scripts.deploy_kubernetes import check_docker
from scripts.kubernetes_manifests import ROOT, manifests
from scripts.upgrade_kubernetes import migration_job


def test_upgrade_migrates_without_bootstrapping_or_replacing_configuration():
    job = migration_job()
    assert job["metadata"]["name"] == "relay-upgrade-v4"
    spec = job["spec"]["template"]["spec"]
    assert "initContainers" not in spec
    assert len(spec["containers"]) == 1
    migration = spec["containers"][0]
    assert migration["command"] == ["python", "-m", "backend.manage", "migrate"]
    assert migration["env"][0]["valueFrom"]["secretKeyRef"]["key"] == "migration-url"
    assert "bootstrap-password" not in json.dumps(job)


def test_kubernetes_runtime_has_shared_storage_and_limited_credentials():
    groups = manifests()
    backend = next(
        x for x in groups["workloads"] if x["kind"] == "Deployment" and x["metadata"]["name"] == "backend"
    )
    spec = backend["spec"]
    assert spec["replicas"] == 1 and spec["strategy"]["type"] == "Recreate"
    pod = spec["template"]["spec"]
    assert not pod["automountServiceAccountToken"]
    assert {c["name"] for c in pod["containers"]} == {"api", "worker", "scheduler"}
    for container in pod["containers"]:
        assert container["volumeMounts"] == [{"name": "documents", "mountPath": "/data"}]
        assert container["env"][0]["valueFrom"]["secretKeyRef"]["key"] == "application-url"
        assert not container["securityContext"]["allowPrivilegeEscalation"]
    assert "migration-url" not in json.dumps(groups["workloads"])
    setup = groups["setup"][0]["spec"]["template"]["spec"]
    assert setup["initContainers"][0]["env"][0]["valueFrom"]["secretKeyRef"]["key"] == "migration-url"
    assert setup["restartPolicy"] == "Never"


def test_checked_in_manifests_match_renderer_and_contain_no_secret_values():
    for name, items in manifests().items():
        checked_in = json.loads((ROOT / f"ops/kubernetes/{name}.json").read_text())
        assert checked_in["items"] == items
        assert not any(item["kind"] == "Secret" for item in items)


def test_live_kubernetes_separates_models_and_keeps_credentials_in_secrets():
    groups = manifests(live=True, bootstrap_email="operator@company.example")
    config = next(x["data"] for x in groups["infrastructure"] if x["metadata"]["name"] == "relay-config")
    assert config["MODEL_PROVIDER"] == "openai"
    assert config["CHAT_MODEL"] == "gpt-5.6-luna"
    assert config["GRADER_MODEL"] == "gpt-5.6-terra"
    assert config["EMBEDDING_PROVIDER"] == "local" and config["EMBEDDING_DIMENSIONS"] == "384"
    assert "ALLOW_DEMO_PUBLICATION" not in config
    assert "OPENAI_API_KEY" not in config
    setup = groups["setup"][0]["spec"]["template"]["spec"]
    assert setup["containers"][0]["command"][-1] == "bootstrap"
    assert "relay-live-bootstrap" in json.dumps(setup)
    backend = next(x for x in groups["workloads"] if x["metadata"]["name"] == "backend")
    containers = backend["spec"]["template"]["spec"]["containers"]
    for container in containers:
        api_key = next(e for e in container["env"] if e["name"] == "OPENAI_API_KEY")
        assert api_key["valueFrom"]["secretKeyRef"] == {"name": "relay-openai", "key": "api-key"}
    assert next(c for c in containers if c["name"] == "worker")["command"][-1] == "--concurrency=2"


@pytest.mark.parametrize(
    "output,code,error",
    [
        ("linux\n", 0, None),
        ("windows\n", 0, "requires Linux containers"),
        ("", 1, "Linux engine is unavailable"),
        ("Docker Desktop is manually paused", 0, "Docker Desktop is paused"),
    ],
)
def test_docker_preflight_reports_actionable_errors(monkeypatch, output, code, error):
    monkeypatch.setattr(
        "scripts.deploy_kubernetes.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], code, stdout=output, stderr="private diagnostic"
        ),
    )
    if error:
        with pytest.raises(RuntimeError, match=error) as failure:
            check_docker(0)
        assert "private diagnostic" not in str(failure.value)
    else:
        check_docker(0)


def test_docker_preflight_bounds_hung_engine(monkeypatch):
    def stalled(*args, **kwargs):
        assert kwargs["timeout"] <= 10
        raise subprocess.TimeoutExpired("docker", kwargs["timeout"])

    monkeypatch.setattr("scripts.deploy_kubernetes.subprocess.run", stalled)
    with pytest.raises(RuntimeError, match="Linux engine is unavailable"):
        check_docker(0)
