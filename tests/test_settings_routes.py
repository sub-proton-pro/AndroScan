"""Integration tests for /api/settings/* and /api/status/*."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from androscan.config import Config
from androscan.web import health_probes as hp
from androscan.web.app import create_app


@pytest.fixture
def cfg() -> Config:
    return Config.default()


@pytest.fixture
def client(cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()
    # Stub out *all* probes that would touch the network or adb so the test
    # runner doesn't depend on Ollama / a real device being up.
    async def _ok_tags(*a, **kw):
        return {"ok": True, "reachable": True, "url": "http://localhost:11434/api/tags",
                "ping_ms": 1, "models": [cfg.ollama_model, "nomic-embed-text"], "error": None}
    async def _ok_llamacpp(*a, **kw):
        # Default fixture stub for the LCP.3 llama.cpp probe — never
        # exercised by the default Ollama provider_kind path, but
        # tests that flip the provider via .config swap rely on this
        # stub being in place so the request doesn't fall through to
        # a real network call against 127.0.0.1:8033.
        return {"ok": True, "reachable": True,
                "url": "http://127.0.0.1:8033/v1/models",
                "ping_ms": 1, "models": ["local-llamacpp"], "error": None}
    async def _missing(*a, **kw):
        return {"ok": False, "found": False, "cmd": "x", "path": None,
                "version": None, "error": "stubbed"}
    async def _empty_dump(*a, **kw):
        return {"ok": False, "rc": -1, "has_xml": False, "error": "stubbed"}
    async def _empty_pkg(*a, **kw):
        return {"ok": True, "running": False, "package": "", "pid": None, "error": None}
    async def _fg(*a, **kw):
        return {"ok": False, "activity": None, "package": None, "error": "stubbed"}
    async def _pkg_install(*a, **kw):
        return {"ok": False, "installed": False, "package": "", "error": "stubbed"}
    async def _pkg_uid(*a, **kw):
        return {"ok": False, "resolved": False, "uid": None, "method": None, "error": "stubbed"}
    async def _device_ok(*a, **kw):
        # Default fixture: a healthy device is attached so per-app device
        # probes are actually exercised. Tests that need the "no device"
        # path can override this via monkeypatch.
        return {"ok": True, "connected": True, "state": "device",
                "serial": "emulator-5554", "error": None}
    async def _frida_server_off(*a, **kw):
        # Hook Lab readiness defaults to "device half not running" so the
        # version-skew probe short-circuits (and the test runner doesn't
        # need to think about adb at all).
        return {"ok": False, "running": False, "pid": None,
                "error": "frida-server not running on device"}
    async def _abi_arm64(*a, **kw):
        # Default fixture: pretend the attached emulator reports the
        # standard 64-bit ARM ABI so the frida-server install playbook
        # is fully populated (matches the real device used during
        # development; tests that need the "unknown ABI" or "no device"
        # paths override via monkeypatch).
        return {"ok": True, "abi": "arm64-v8a", "frida_arch": "android-arm64",
                "error": None}
    async def _root_user_build(*a, **kw):
        # Default fixture: production / Google Play AVD posture (the
        # most common setup operators land on by accident). Mirrors the
        # real device used during development — locks the UI's "warn
        # before adb root" path into the contract test below. Tests
        # that need a userdebug AVD or a Magisk-rooted device override
        # via monkeypatch.
        return {"ok": True, "rooted": False, "can_adb_root": False,
                "current_uid": 2000, "build_type": "user",
                "debuggable": False, "error": None}
    # Patch via the *consumer* module — status_routes binds the names at
    # import time, so patching the producer module wouldn't take effect.
    from androscan.web import status_routes as sr
    monkeypatch.setattr(sr, "probe_ollama_tags", _ok_tags)
    monkeypatch.setattr(sr, "probe_llamacpp", _ok_llamacpp)
    monkeypatch.setattr(sr, "probe_adb_version", _missing)
    monkeypatch.setattr(sr, "probe_jadx_version", _missing)
    monkeypatch.setattr(sr, "probe_apktool_version", _missing)
    monkeypatch.setattr(sr, "probe_frida_version", _missing)
    monkeypatch.setattr(sr, "probe_frida_server", _frida_server_off)
    monkeypatch.setattr(sr, "probe_device_cpu_abi", _abi_arm64)
    monkeypatch.setattr(sr, "probe_device_root_status", _root_user_build)
    monkeypatch.setattr(sr, "probe_adb_device", _device_ok)
    monkeypatch.setattr(sr, "probe_uiautomator_dump", _empty_dump)
    monkeypatch.setattr(sr, "probe_foreground_activity", _fg)
    monkeypatch.setattr(sr, "probe_pkg_installed", _pkg_install)
    monkeypatch.setattr(sr, "probe_pkg_running", _empty_pkg)
    monkeypatch.setattr(sr, "probe_pkg_uid", _pkg_uid)
    # Also bust the in-process cache between tests.
    from androscan.web.status_routes import invalidate_status_cache
    invalidate_status_cache()
    app = create_app(cfg, cwd=tmp_path)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/settings/global


def test_get_global_settings(client: TestClient) -> None:
    r = client.get("/api/settings/global")
    assert r.status_code == 200
    body = r.json()
    assert "global" in body
    assert "flat" in body
    assert "sources" in body
    assert "field_map" in body
    assert "ollama_model" in body["flat"]


def test_put_global_settings_partial(client: TestClient) -> None:
    r = client.put(
        "/api/settings/global",
        json={"fields": {"ollama_model": "qwen2:7b", "rag_top_k_default": 16}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "ollama_model" in body["updated_fields"]
    # Now /global should reflect the updated value.
    r2 = client.get("/api/settings/global")
    assert r2.json()["flat"]["ollama_model"] == "qwen2:7b"
    assert r2.json()["flat"]["rag_top_k_default"] == 16
    # /api/llm/info still shows boot-time captured config (closure capture):
    # since put_global swaps app.state.config, our check confirms the new
    # routers use it. The legacy /api/llm/info uses the closure, so we
    # don't assert on it here.


def test_put_global_settings_unknown_field_rejected(client: TestClient) -> None:
    r = client.put("/api/settings/global", json={"fields": {"nope": 1}})
    assert r.status_code == 400


def test_put_global_settings_env_locked_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANDROSCAN_OLLAMA_MODEL", "qwen2:7b")
    r = client.put("/api/settings/global", json={"fields": {"ollama_model": "x"}})
    assert r.status_code == 409
    assert "env" in r.text.lower() or "locked" in r.text.lower()


def test_put_global_raw_yaml_replaces_file(client: TestClient, tmp_path: Path) -> None:
    raw = "ollama:\n  model: qwen2:7b\n  base_url: http://1.2.3.4:11434\n"
    r = client.put("/api/settings/global/raw", json={"raw_yaml": raw})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    yml = (tmp_path / "global_config.yaml").read_text(encoding="utf-8")
    assert "qwen2:7b" in yml


def test_put_global_raw_yaml_invalid_400(client: TestClient) -> None:
    r = client.put("/api/settings/global/raw", json={"raw_yaml": ":\n  - {{bad: ["})
    assert r.status_code == 400


def test_post_global_reset_writes_defaults(client: TestClient, tmp_path: Path) -> None:
    # First mutate, then reset.
    client.put("/api/settings/global", json={"fields": {"ollama_model": "qwen2:7b"}})
    r = client.post("/api/settings/global/reset")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["global"]["flat"]["ollama_model"] == Config.default().ollama_model


def test_post_reload_after_external_edit(client: TestClient, tmp_path: Path) -> None:
    """User edits global_config.yaml outside the UI; reload should pick it up."""
    cfg_path = tmp_path / "global_config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"ollama": {"model": "external-edit:7b"}}), encoding="utf-8"
    )
    r = client.post("/api/settings/reload")
    assert r.status_code == 200
    assert r.json()["global"]["flat"]["ollama_model"] == "external-edit:7b"


# ---------------------------------------------------------------------------
# /api/settings/apps/{app_id}


def _mkapp(tmp_path: Path) -> str:
    app_dir = tmp_path / "apps" / "com.example"
    app_dir.mkdir(parents=True, exist_ok=True)
    return "com.example"


def test_get_app_settings_default(client: TestClient, tmp_path: Path) -> None:
    app_id = _mkapp(tmp_path)
    r = client.get(f"/api/settings/apps/{app_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["app_id"] == app_id
    assert body["per_app"]["rag"] == {}
    # Effective view should show globals as the source.
    assert body["effective"]["rag"]["embed_provider"]["source"] == "global"


def test_put_app_settings_persists(client: TestClient, tmp_path: Path) -> None:
    app_id = _mkapp(tmp_path)
    r = client.put(
        f"/api/settings/apps/{app_id}",
        json={"patch": {"rag": {"embed_provider": "ollama"}, "tags": ["banking"]}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["per_app"]["rag"]["embed_provider"] == "ollama"
    assert body["per_app"]["tags"] == ["banking"]
    on_disk = json.loads((tmp_path / "apps" / app_id / "app_settings.json").read_text())
    assert on_disk["rag"]["embed_provider"] == "ollama"
    assert body["effective"]["rag"]["embed_provider"]["source"] == "app"


def test_put_app_settings_unknown_key_400(client: TestClient, tmp_path: Path) -> None:
    app_id = _mkapp(tmp_path)
    r = client.put(f"/api/settings/apps/{app_id}", json={"patch": {"nope": 1}})
    assert r.status_code == 400


def test_post_reset_app_settings(client: TestClient, tmp_path: Path) -> None:
    app_id = _mkapp(tmp_path)
    client.put(
        f"/api/settings/apps/{app_id}",
        json={"patch": {"rag": {"embed_provider": "ollama"}}},
    )
    r = client.post(f"/api/settings/apps/{app_id}/reset")
    assert r.status_code == 200
    assert r.json()["per_app"]["rag"] == {}


def test_put_app_settings_unknown_app_404(client: TestClient) -> None:
    r = client.put("/api/settings/apps/does_not_exist", json={"patch": {"tags": ["x"]}})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/status/*


def test_get_global_status(client: TestClient) -> None:
    r = client.get("/api/status/global")
    assert r.status_code == 200
    body = r.json()
    for key in ("process", "tools", "device", "llm", "rag_provider", "filesystem", "config_sources"):
        assert key in body
    assert body["llm"]["model_present"] is True  # we stubbed tags to include the model
    assert body["tools"]["adb"]["ok"] is False  # stubbed missing
    # Default fixture stubs a healthy emulator.
    assert body["device"]["ok"] is True
    assert body["device"]["connected"] is True
    assert body["device"]["state"] == "device"
    assert body["device"]["label"] == "Android device / emulator"


def test_get_global_status_default_provider_routes_to_ollama_probe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LCP.3: with the default ``llm_provider="ollama"`` the LLM
    card carries the ``provider: "ollama"`` discriminator and is
    sourced from :func:`probe_ollama_tags` only — the llama.cpp
    probe MUST NOT have been called."""
    from androscan.web import status_routes as sr
    from androscan.web.status_routes import invalidate_status_cache

    ollama_calls: list[tuple] = []
    llamacpp_calls: list[tuple] = []

    async def _spy_ollama(*a, **kw):
        ollama_calls.append((a, kw))
        return {"ok": True, "reachable": True, "url": "http://localhost:11434/api/tags",
                "ping_ms": 1, "models": ["qwen3.5:35b", "nomic-embed-text"], "error": None}
    async def _spy_llamacpp(*a, **kw):
        llamacpp_calls.append((a, kw))
        return {"ok": True, "reachable": True, "url": "http://127.0.0.1:8033/v1/models",
                "ping_ms": 1, "models": ["should-never-be-seen"], "error": None}

    monkeypatch.setattr(sr, "probe_ollama_tags", _spy_ollama)
    monkeypatch.setattr(sr, "probe_llamacpp", _spy_llamacpp)
    invalidate_status_cache()

    body = client.get("/api/status/global").json()
    assert body["llm"]["provider"] == "ollama"
    assert body["llm"]["label"] == "LLM (Ollama)"
    assert body["llm"]["base_url"] == "http://localhost:11434/api/tags"
    assert len(ollama_calls) == 1
    assert len(llamacpp_calls) == 0  # mutual exclusion guard


def test_get_global_status_llamacpp_provider_routes_to_llamacpp_probe(
    cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LCP.3: with ``llm_provider="llamacpp"`` the LLM card carries
    the ``provider: "llamacpp"`` discriminator and is sourced from
    :func:`probe_llamacpp`. The Ollama probe MUST NOT have been
    called — the operator picked one local LLM, we probe one local
    LLM."""
    import dataclasses
    from androscan.web import status_routes as sr
    from androscan.web.app import create_app
    from androscan.web.status_routes import invalidate_status_cache

    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps").mkdir()

    ollama_calls: list[tuple] = []
    llamacpp_calls: list[tuple] = []

    async def _spy_ollama(*a, **kw):
        ollama_calls.append((a, kw))
        return {"ok": False, "reachable": False, "url": "should-never-render",
                "ping_ms": 0, "models": [], "error": "should-never-render"}
    async def _spy_llamacpp(*a, **kw):
        llamacpp_calls.append((a, kw))
        return {"ok": True, "reachable": True,
                "url": "http://127.0.0.1:8033/v1/models",
                "ping_ms": 2, "models": ["qwen3-27b-q5km"], "error": None}

    # Stub all the other probes the fixture would normally stub.
    async def _missing(*a, **kw):
        return {"ok": False, "found": False, "cmd": "x", "path": None,
                "version": None, "error": "stubbed"}
    async def _device_off(*a, **kw):
        return {"ok": False, "connected": False, "state": None,
                "serial": None, "error": "no device attached"}
    async def _frida_off(*a, **kw):
        return {"ok": False, "running": False, "pid": None,
                "error": "frida-server not running on device"}
    async def _abi(*a, **kw):
        return {"ok": False, "abi": None, "frida_arch": None, "error": "no device"}
    async def _root(*a, **kw):
        return {"ok": False, "rooted": False, "can_adb_root": False,
                "current_uid": None, "build_type": None, "debuggable": False,
                "error": "no device"}

    monkeypatch.setattr(sr, "probe_ollama_tags", _spy_ollama)
    monkeypatch.setattr(sr, "probe_llamacpp", _spy_llamacpp)
    monkeypatch.setattr(sr, "probe_adb_version", _missing)
    monkeypatch.setattr(sr, "probe_jadx_version", _missing)
    monkeypatch.setattr(sr, "probe_apktool_version", _missing)
    monkeypatch.setattr(sr, "probe_frida_version", _missing)
    monkeypatch.setattr(sr, "probe_frida_server", _frida_off)
    monkeypatch.setattr(sr, "probe_device_cpu_abi", _abi)
    monkeypatch.setattr(sr, "probe_device_root_status", _root)
    monkeypatch.setattr(sr, "probe_adb_device", _device_off)
    invalidate_status_cache()

    cfg_llamacpp = dataclasses.replace(cfg, llm_provider="llamacpp")
    app = create_app(cfg_llamacpp, cwd=tmp_path)
    client = TestClient(app)

    body = client.get("/api/status/global").json()
    assert body["llm"]["provider"] == "llamacpp"
    assert body["llm"]["label"] == "LLM (llama.cpp)"
    assert body["llm"]["base_url"] == "http://127.0.0.1:8033/v1/models"
    assert body["llm"]["models_available"] == ["qwen3-27b-q5km"]
    # No llamacpp_model field on Config until LCP.4 — the helper
    # accepts any loaded model so the card flips green.
    assert body["llm"]["ok"] is True
    assert body["llm"]["model_present"] is True
    assert len(llamacpp_calls) == 1
    assert len(ollama_calls) == 0  # mutual exclusion guard


def test_get_global_status_no_device(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When adb reports no device, the global card is not ok and surfaces a hint."""
    from androscan.web import status_routes as sr
    from androscan.web.status_routes import invalidate_status_cache

    async def _no_device(*a, **kw):
        return {"ok": False, "connected": False, "state": None, "serial": None,
                "error": "adb: no devices/emulators found"}
    monkeypatch.setattr(sr, "probe_adb_device", _no_device)
    invalidate_status_cache()

    body = client.get("/api/status/global").json()
    assert body["device"]["ok"] is False
    assert body["device"]["connected"] is False
    assert body["device"]["hint"]
    assert "no devices" in body["device"]["error"]


def test_get_per_app_status(client: TestClient, tmp_path: Path) -> None:
    app_id = _mkapp(tmp_path)
    # Drop a minimal app_meta.json so the meta_card has something to show.
    (tmp_path / "apps" / app_id / "app_meta.json").write_text(
        json.dumps({
            "apk_path": str(tmp_path / "fake.apk"),
            "apk_sha256": "deadbeef" * 8,
            "dossier": {"apk_info": {"package": "com.example"}},
        }),
        encoding="utf-8",
    )
    r = client.get(f"/api/status/apps/{app_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_id"] == app_id
    assert body["meta"]["package"] == "com.example"
    # decompile not run yet → status missing/unknown depending on path
    assert body["decompile"]["status"] in ("missing", "unknown")
    # rag follows decompile
    assert body["rag"]["status"] in ("missing", "pending", "failed")
    assert "device" in body
    # Default fixture: device is attached, so device-side cards are populated
    # (real cards, not skipped placeholders).
    assert body["device"]["connected"] is True
    assert body["device"]["state"] == "device"
    for k in ("package_installed", "package_running", "package_uid",
              "foreground", "uiautomator_dump"):
        assert k in body["device"]
        assert body["device"][k].get("skipped") is not True
    assert "overrides" in body


def test_get_per_app_status_skips_device_probes_when_no_device(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No device attached → device-side cards are returned as skipped warns,
    not as five independent reds, and the underlying adb-shell probes are
    not invoked at all."""
    from androscan.web import status_routes as sr
    from androscan.web.status_routes import invalidate_status_cache

    async def _no_device(*a, **kw):
        return {"ok": False, "connected": False, "state": None, "serial": None,
                "error": "adb: no devices/emulators found"}

    called: dict[str, int] = {}

    def _track(name: str):
        async def _stub(*a, **kw):
            called[name] = called.get(name, 0) + 1
            return {"ok": False, "error": "should not be called"}
        return _stub

    monkeypatch.setattr(sr, "probe_adb_device", _no_device)
    monkeypatch.setattr(sr, "probe_pkg_installed", _track("pkg_installed"))
    monkeypatch.setattr(sr, "probe_pkg_running", _track("pkg_running"))
    monkeypatch.setattr(sr, "probe_pkg_uid", _track("pkg_uid"))
    monkeypatch.setattr(sr, "probe_foreground_activity", _track("fg"))
    monkeypatch.setattr(sr, "probe_uiautomator_dump", _track("ui"))
    invalidate_status_cache()

    app_id = _mkapp(tmp_path)
    (tmp_path / "apps" / app_id / "app_meta.json").write_text(
        json.dumps({
            "apk_path": str(tmp_path / "fake.apk"),
            "apk_sha256": "deadbeef" * 8,
            "dossier": {"apk_info": {"package": "com.example"}},
        }),
        encoding="utf-8",
    )
    body = client.get(f"/api/status/apps/{app_id}").json()
    dev = body["device"]
    assert dev["connected"] is False
    for k in ("package_installed", "package_running", "package_uid",
              "foreground", "uiautomator_dump"):
        card = dev[k]
        assert card["skipped"] is True, f"{k} should be marked skipped"
        assert card["ok"] is False
        assert "no devices" in (card["error"] or "")
    # And none of the per-app device probes should have been called.
    assert called == {}


def test_status_cache_returns_stale_within_ttl(client: TestClient) -> None:
    """A second call within TTL hits the cache (faster, identical payload)."""
    r1 = client.get("/api/status/global").json()
    r2 = client.get("/api/status/global").json()
    assert r1["ts"] == r2["ts"]  # identical → from cache


def test_global_status_exposes_frida_server_card_contract(client: TestClient) -> None:
    """Hook Lab readiness card (DEC-023) must match the SettingsTab contract.

    The frontend (`src/api/status.ts` ``GlobalStatus.tools.frida_server``)
    relies on this exact field set: ``ok``, ``label``, ``running``, ``pid``,
    ``host_version``, ``device_version``, ``version_skew``, ``error``,
    ``device_abi``, ``frida_arch``, ``device_rooted``, ``can_adb_root``,
    ``device_build_type``. Locking the keys here means any backend
    rename that drifts the contract breaks pytest before it ever ships
    to the UI.
    """
    body = client.get("/api/status/global").json()
    card = body["tools"]["frida_server"]
    expected_keys = {
        "ok", "label", "running", "pid",
        "host_version", "device_version", "version_skew", "error",
        "device_abi", "frida_arch",
        "device_rooted", "can_adb_root", "device_build_type",
    }
    assert expected_keys.issubset(card.keys()), (
        f"missing keys: {expected_keys - card.keys()}"
    )
    # Default fixture has frida-server off; card must be red and clearly say so.
    assert card["ok"] is False
    assert card["running"] is False
    assert card["pid"] is None
    assert card["version_skew"] is None
    assert card["label"] == "frida-server (device)"
    assert "frida-server" in (card["error"] or "")
    # Default fixture pretends an arm64 emulator is attached, so the
    # install playbook fields must be populated and self-consistent.
    assert card["device_abi"] == "arm64-v8a"
    assert card["frida_arch"] == "android-arm64"
    # Default fixture is the production / Google Play AVD posture
    # (build=user, uid=2000) — the UI relies on can_adb_root=False to
    # render the "this AVD can't be rooted" warning before step 4.
    assert card["device_build_type"] == "user"
    assert card["device_rooted"] is False
    assert card["can_adb_root"] is False


def test_global_status_frida_server_card_userdebug_avd(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AOSP / Google APIs AVD: can_adb_root=True, no warning surfaced."""
    from androscan.web import status_routes as sr
    from androscan.web.status_routes import invalidate_status_cache

    async def _userdebug(*a, **kw):
        return {"ok": True, "rooted": False, "can_adb_root": True,
                "current_uid": 2000, "build_type": "userdebug",
                "debuggable": True, "error": None}

    monkeypatch.setattr(sr, "probe_device_root_status", _userdebug)
    invalidate_status_cache()

    card = client.get("/api/status/global").json()["tools"]["frida_server"]
    assert card["can_adb_root"] is True
    assert card["device_build_type"] == "userdebug"
    assert card["device_rooted"] is False


def test_global_status_frida_server_card_no_device_root(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No device attached → all three root fields are null (not raising)."""
    from androscan.web import status_routes as sr
    from androscan.web.status_routes import invalidate_status_cache

    async def _no_device_root(*a, **kw):
        return {"ok": False, "rooted": None, "can_adb_root": None,
                "current_uid": None, "build_type": None, "debuggable": None,
                "error": "adb: no devices/emulators found"}

    monkeypatch.setattr(sr, "probe_device_root_status", _no_device_root)
    invalidate_status_cache()

    card = client.get("/api/status/global").json()["tools"]["frida_server"]
    assert card["device_rooted"] is None
    assert card["can_adb_root"] is None
    assert card["device_build_type"] is None


def test_global_status_frida_server_card_unknown_abi(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unmapped device ABI → ``device_abi`` surfaced but ``frida_arch`` is null.

    The Settings tab uses ``frida_arch is None`` as the gate for "we
    can't synthesise a download URL — link the operator to the releases
    page directly". Locking the contract so the UI degrades gracefully.
    """
    from androscan.web import status_routes as sr
    from androscan.web.status_routes import invalidate_status_cache

    async def _exotic_abi(*a, **kw):
        return {"ok": False, "abi": "riscv64", "frida_arch": None,
                "error": "unknown ABI 'riscv64' (no Frida arch mapping)"}

    monkeypatch.setattr(sr, "probe_device_cpu_abi", _exotic_abi)
    invalidate_status_cache()

    card = client.get("/api/status/global").json()["tools"]["frida_server"]
    assert card["device_abi"] == "riscv64"
    assert card["frida_arch"] is None


def test_global_status_frida_server_card_no_device_abi(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No device attached → both ABI fields are null (not raising / 500-ing)."""
    from androscan.web import status_routes as sr
    from androscan.web.status_routes import invalidate_status_cache

    async def _no_device_abi(*a, **kw):
        return {"ok": False, "abi": None, "frida_arch": None,
                "error": "adb: no devices/emulators found"}

    monkeypatch.setattr(sr, "probe_device_cpu_abi", _no_device_abi)
    invalidate_status_cache()

    card = client.get("/api/status/global").json()["tools"]["frida_server"]
    assert card["device_abi"] is None
    assert card["frida_arch"] is None


def test_global_status_frida_server_card_running_with_skew(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When frida-server is up but versions disagree, severity is surfaced."""
    from androscan.web import status_routes as sr
    from androscan.web.status_routes import invalidate_status_cache

    async def _running(*a, **kw):
        return {"ok": True, "running": True, "pid": 1234, "error": None}

    async def _major_skew(*a, **kw):
        return {
            "ok": False,
            "host_version": "16.4.10",
            "device_version": "15.2.0",
            "severity": "major",
            "error": "major version mismatch (host 16.4.10, device 15.2.0)",
        }

    async def _frida_host(*a, **kw):
        return {"ok": True, "found": True, "cmd": "frida", "path": "/fake/frida",
                "version": "16.4.10", "error": None}

    monkeypatch.setattr(sr, "probe_frida_server", _running)
    monkeypatch.setattr(sr, "probe_frida_version_skew", _major_skew)
    monkeypatch.setattr(sr, "probe_frida_version", _frida_host)
    invalidate_status_cache()

    card = client.get("/api/status/global").json()["tools"]["frida_server"]
    assert card["running"] is True
    assert card["pid"] == 1234
    assert card["host_version"] == "16.4.10"
    assert card["device_version"] == "15.2.0"
    assert card["version_skew"] == "major"
    assert card["ok"] is False  # major skew takes the card red
