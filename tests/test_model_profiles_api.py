"""API tests for GET/PUT /v1/model-profiles (T-089)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brain.active_model import write_active_profile
from brain.config import Settings
from brain.main import create_app
from tests.test_api import ScriptedOllama


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return Settings(
        location={"latitude": 1.0, "longitude": 2.0},
        ollama={
            "url": "http://test",
            "model": "qwen3:8b",
            "active_profile": "default",
            "profiles": {
                "default": {"model": "qwen3:8b"},
                "trial": {"model": "qwen3:14b", "num_ctx": 4096},
            },
        },
        runtime={"data_dir": data_dir, "log_level": "WARNING"},
        agent={"max_iterations": 3},
        timeouts={"ollama_s": 30, "tool_s": 5, "turn_s": 60},
    )


def _client(settings: Settings) -> TestClient:
    ollama = ScriptedOllama([])
    return TestClient(
        create_app(
            settings,
            client=ollama,  # type: ignore[arg-type]
            system_prompt="You are Mimir.",
            prompt_id="test:prompt",
            data_dir=settings.runtime.data_dir,
        )
    )


def test_model_profiles_get_shape(settings: Settings) -> None:
    with _client(settings) as tc:
        resp = tc.get("/v1/model-profiles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_profile"] == "default"
    assert body["loaded_profile"] == "default"
    assert body["loaded_model"] == "qwen3:8b"
    assert body["restart_pending"] is False
    names = {p["name"] for p in body["profiles"]}
    assert names == {"default", "trial"}
    trial = next(p for p in body["profiles"] if p["name"] == "trial")
    assert trial["model"] == "qwen3:14b"
    assert trial["num_ctx"] == 4096


def test_model_profiles_put_and_restart_pending(settings: Settings) -> None:
    with _client(settings) as tc:
        put = tc.put("/v1/model-profiles/active", json={"profile": "trial"})
        assert put.status_code == 200
        out = put.json()
        assert out["active_profile"] == "trial"
        assert out["model"] == "qwen3:14b"
        assert out["changed"] is True
        assert out["restart_required"] is True

        got = tc.get("/v1/model-profiles").json()
        assert got["active_profile"] == "trial"
        assert got["loaded_profile"] == "default"
        assert got["restart_pending"] is True


def test_model_profiles_put_unknown(settings: Settings) -> None:
    with _client(settings) as tc:
        resp = tc.put("/v1/model-profiles/active", json={"profile": "nope"})
    assert resp.status_code == 400


def test_model_profiles_put_idempotent(settings: Settings) -> None:
    write_active_profile(settings.runtime.data_dir, "default")
    with _client(settings) as tc:
        resp = tc.put("/v1/model-profiles/active", json={"profile": "default"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["changed"] is False
    assert body["restart_required"] is False


def test_model_profiles_get_sticky_mismatch(settings: Settings) -> None:
    write_active_profile(settings.runtime.data_dir, "trial")
    with _client(settings) as tc:
        body = tc.get("/v1/model-profiles").json()
    assert body["active_profile"] == "trial"
    assert body["loaded_profile"] == "default"
    assert body["restart_pending"] is True
