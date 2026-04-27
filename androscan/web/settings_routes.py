"""HTTP routes for the Settings tab — global YAML editor + per-app overrides.

Endpoints
---------
* ``GET    /api/settings/global``                 effective + raw YAML + sources.
* ``PUT    /api/settings/global``                 partial structured update (flat fields).
* ``PUT    /api/settings/global/raw``             replace YAML with user-typed text.
* ``POST   /api/settings/global/reset``           restore defaults.
* ``POST   /api/settings/reload``                 re-read YAML from disk into ``app.state.config``.
* ``GET    /api/settings/apps/{app_id}``          per-app effective view.
* ``PUT    /api/settings/apps/{app_id}``          partial update of app_settings.json.
* ``POST   /api/settings/apps/{app_id}/reset``    wipe overrides.

The UI uses the structured ``PUT`` for normal field edits and the
``raw`` variant for the "edit YAML" mode (per the user's choice — they
want a YAML editor *and* form-style inputs).

Live-reload: ``POST /reload`` re-runs :func:`load_config` and assigns the
new ``Config`` to ``app.state.config``. Routers that captured the
``config_provider`` callable (status_routes, the future settings consumers
in app.py) pick up the new value on their next call. Anything that
captured the old config snapshot at boot (uvicorn host/port, the CORS
allow-list) keeps the stale value — the response includes a
``restart_required`` flag listing those fields so the UI can warn.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from androscan.config import Config, load_config
from androscan.config.loader import (
    CONFIG_FIELD_MAP,
    LIVE_RELOADABLE_FIELDS,
    config_as_flat_dict,
    discover_config_path,
    dump_to_yaml,
    effective_sources,
    env_overrides,
    global_view_from_config,
    read_raw_yaml,
    restore_defaults_yaml,
    save_raw_yaml,
)
from androscan.internal.app_meta import load_app_meta
from androscan.web.per_app_settings import (
    coerce_partial_update,
    default_app_settings,
    effective_settings,
    load_app_settings,
    reset_app_settings,
    save_app_settings,
)
from androscan.web.status_routes import invalidate_status_cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request bodies


class GlobalUpdateBody(BaseModel):
    """Partial structured update.

    The UI sends only the fields the user actually changed; we round-trip
    them through ``CONFIG_FIELD_MAP`` and merge into the YAML. Unknown keys
    return 400 (so a typo doesn't silently no-op).
    """
    fields: dict[str, Any] = Field(default_factory=dict)


class GlobalRawBody(BaseModel):
    """Raw YAML editor body. Validation runs server-side via :func:`save_raw_yaml`."""
    raw_yaml: str = Field(..., max_length=200_000)


class AppSettingsUpdateBody(BaseModel):
    """Partial per-app patch (sections rag/decompile/inspect/exploit/chat + tags + notes)."""
    patch: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers


def _global_payload(config: Config, config_path: Path) -> dict[str, Any]:
    """Bundle config + introspection metadata into one response shape."""
    raw_yaml = read_raw_yaml(config_path)
    return {
        "ts": time.time(),
        "config_path": str(config_path),
        "config_path_exists": config_path.is_file(),
        "global": global_view_from_config(config),
        "flat": config_as_flat_dict(config),
        "raw_yaml": raw_yaml,
        "sources": effective_sources(config_path),
        "env_locks": env_overrides(),
        "live_reloadable": sorted(LIVE_RELOADABLE_FIELDS),
        "field_map": {
            field: {"section": sec, "key": key, "env_var": env}
            for field, (sec, key, env) in CONFIG_FIELD_MAP.items()
        },
    }


def _restart_required_after(updated_fields: list[str]) -> list[str]:
    """Subset of ``updated_fields`` that needs a uvicorn restart to take effect."""
    return sorted(f for f in updated_fields if f not in LIVE_RELOADABLE_FIELDS)


# ---------------------------------------------------------------------------
# Router factory


def build_settings_router(
    config_provider: Callable[[], Config],
    set_config: Callable[[Config], None],
    apps_root_provider: Callable[[], Path],
    app_dir_resolver: Callable[[str], Path],
    config_path_provider: Optional[Callable[[], Path]] = None,
) -> APIRouter:
    """Return the ``/api/settings/*`` router.

    ``set_config`` swaps in the new ``Config`` after a save / reload —
    typically ``lambda c: setattr(app.state, 'config', c)``. Routers that
    read via ``config_provider`` pick up the new value automatically.
    """
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    def _config_path() -> Path:
        if config_path_provider:
            return config_path_provider()
        return discover_config_path()

    # ---- Global ----------------------------------------------------------

    @router.get("/global")
    def get_global() -> dict[str, Any]:
        return _global_payload(config_provider(), _config_path())

    @router.put("/global")
    def put_global(body: GlobalUpdateBody) -> dict[str, Any]:
        if not body.fields:
            raise HTTPException(status_code=400, detail="no fields supplied")
        # Reject env-locked keys up front so the user gets a clear error.
        locked = set(env_overrides().keys())
        locked_fields: list[str] = []
        for f in body.fields:
            if f not in CONFIG_FIELD_MAP:
                raise HTTPException(status_code=400, detail=f"unknown field: {f!r}")
            _sec, _k, env = CONFIG_FIELD_MAP[f]
            if env and env in locked:
                locked_fields.append(f)
        if locked_fields:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"fields locked by environment variables: {locked_fields}; "
                    "unset the env vars or remove these fields from the patch"
                ),
            )
        path = _config_path()
        try:
            dump_to_yaml(path, body.fields)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        new_config = load_config(str(path))
        set_config(new_config)
        invalidate_status_cache()
        return {
            "ok": True,
            "updated_fields": sorted(body.fields.keys()),
            "restart_required": _restart_required_after(list(body.fields.keys())),
            "global": _global_payload(new_config, path),
        }

    @router.put("/global/raw")
    def put_global_raw(body: GlobalRawBody) -> dict[str, Any]:
        path = _config_path()
        try:
            save_raw_yaml(path, body.raw_yaml)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        new_config = load_config(str(path))
        set_config(new_config)
        invalidate_status_cache()
        return {
            "ok": True,
            # We can't tell which fields actually changed in raw mode, so
            # the safest signal to the UI is "anything not live-reloadable
            # might be stale; bounce the server if you changed it".
            "restart_required": sorted(set(CONFIG_FIELD_MAP) - LIVE_RELOADABLE_FIELDS),
            "global": _global_payload(new_config, path),
        }

    @router.post("/global/reset")
    def reset_global() -> dict[str, Any]:
        path = _config_path()
        try:
            restore_defaults_yaml(path)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"failed to write defaults: {e}")
        new_config = load_config(str(path))
        set_config(new_config)
        invalidate_status_cache()
        return {
            "ok": True,
            "restart_required": sorted(set(CONFIG_FIELD_MAP) - LIVE_RELOADABLE_FIELDS),
            "global": _global_payload(new_config, path),
        }

    @router.post("/reload")
    def reload_global() -> dict[str, Any]:
        path = _config_path()
        new_config = load_config(str(path) if path.is_file() else None)
        set_config(new_config)
        invalidate_status_cache()
        return {
            "ok": True,
            "config_path": str(path),
            "global": _global_payload(new_config, path),
        }

    # ---- Per-app ---------------------------------------------------------

    @router.get("/apps/{app_id}")
    def get_app_settings(app_id: str) -> dict[str, Any]:
        app_dir = app_dir_resolver(app_id)
        if not app_dir.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        cfg = config_provider()
        per_app = load_app_settings(app_dir)
        gv = global_view_from_config(cfg)
        pkg = _app_package_from_meta(app_dir)
        eff = effective_settings(global_view=gv, per_app=per_app, app_package=pkg)
        return {
            "ts": time.time(),
            "app_id": app_id,
            "app_dir": str(app_dir),
            "per_app": per_app,
            "effective": eff,
            "global_view": gv,
        }

    @router.put("/apps/{app_id}")
    def put_app_settings(app_id: str, body: AppSettingsUpdateBody) -> dict[str, Any]:
        app_dir = app_dir_resolver(app_id)
        if not app_dir.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        existing = load_app_settings(app_dir)
        merged, err = coerce_partial_update(existing, body.patch)
        if err is not None:
            raise HTTPException(status_code=400, detail=err)
        try:
            persisted = save_app_settings(app_dir, merged)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"failed to write app_settings.json: {e}")
        invalidate_status_cache()
        cfg = config_provider()
        gv = global_view_from_config(cfg)
        pkg = _app_package_from_meta(app_dir)
        return {
            "ok": True,
            "app_id": app_id,
            "per_app": persisted,
            "effective": effective_settings(global_view=gv, per_app=persisted, app_package=pkg),
        }

    @router.post("/apps/{app_id}/reset")
    def reset_app(app_id: str) -> dict[str, Any]:
        app_dir = app_dir_resolver(app_id)
        if not app_dir.is_dir():
            raise HTTPException(status_code=404, detail="Unknown app_id")
        try:
            persisted = reset_app_settings(app_dir)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"failed to reset app_settings.json: {e}")
        invalidate_status_cache()
        cfg = config_provider()
        gv = global_view_from_config(cfg)
        pkg = _app_package_from_meta(app_dir)
        return {
            "ok": True,
            "app_id": app_id,
            "per_app": persisted,
            "effective": effective_settings(global_view=gv, per_app=persisted, app_package=pkg),
        }

    return router


def _app_package_from_meta(app_dir: Path) -> Optional[str]:
    """Return the raw manifest package id for ``app_dir`` (or ``None``).

    The Hook Lab section's ``hook_target_package_prefix`` defaults to
    this string so a freshly-imported app can be hooked without first
    visiting the Settings tab. Best-effort: a missing or corrupt
    ``app_meta.json`` returns ``None`` and the UI surfaces a "set
    prefix" prompt before Inject becomes available.
    """
    meta = load_app_meta(app_dir)
    if not isinstance(meta, dict):
        return None
    dossier = meta.get("dossier") or {}
    apk_info = dossier.get("apk_info") if isinstance(dossier, dict) else None
    if not isinstance(apk_info, dict):
        return None
    pkg = apk_info.get("package")
    if isinstance(pkg, str) and pkg.strip():
        return pkg.strip()
    return None


__all__ = [
    "build_settings_router",
    "default_app_settings",
]
