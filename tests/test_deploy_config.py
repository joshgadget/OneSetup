"""Deployment wiring: CORS origins, and the files the hosts actually read.

A broken deploy config is invisible until a deploy fails, so the contract is asserted
here: the API allows the browser origins it is told to, the Docker image runs the same
app, and the Vercel and Render files point at the right places.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from engine.config import Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]


def test_cors_origins_default_to_the_local_dev_server():
    assert Settings(allowed_origins="").cors_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_cors_origins_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://onesetup.vercel.app, https://www.example.com ")
    get_settings.cache_clear()
    try:
        assert get_settings().cors_origins == [
            "https://onesetup.vercel.app",
            "https://www.example.com",
        ]
    finally:
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        get_settings.cache_clear()


def test_preflight_answers_the_configured_origin_and_not_others():
    from services.api import main as api_main

    client = TestClient(api_main.app)
    allowed = api_main.CORS_ORIGINS[0]
    ok = client.options(
        "/health",
        headers={"Origin": allowed, "Access-Control-Request-Method": "GET"},
    )
    assert ok.status_code in (200, 204)
    assert ok.headers.get("access-control-allow-origin") == allowed

    blocked = client.options(
        "/health",
        headers={"Origin": "https://not-allowed.example", "Access-Control-Request-Method": "GET"},
    )
    assert blocked.headers.get("access-control-allow-origin") != "https://not-allowed.example"


def test_the_docker_image_runs_the_same_app_with_a_volume_backed_store():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "uvicorn services.api.main:app" in dockerfile
    assert "DB_PATH=/data/trading.sqlite3" in dockerfile
    assert 'VOLUME ["/data"]' in dockerfile


def test_vercel_builds_the_web_app_from_its_own_directory():
    config = json.loads((ROOT / "apps/web/vercel.json").read_text(encoding="utf-8"))
    assert config["framework"] == "vite"
    assert config["outputDirectory"] == "dist"
    assert (ROOT / "apps/web/.env.example").exists()


def test_render_blueprint_points_at_the_health_check_and_the_disk():
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    service = blueprint["services"][0]
    assert service["healthCheckPath"] == "/health"
    assert service["dockerfilePath"] == "./Dockerfile"
    assert service["disk"]["mountPath"] == "/data"
