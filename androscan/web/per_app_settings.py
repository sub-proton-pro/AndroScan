"""Per-app settings persisted at ``apps/<app_id>/app_settings.json``.

Kept deliberately separate from ``app_meta.json`` (analysis pipeline output;
should not be hand-edited) so users can edit overrides freely from the
Settings tab without risking dossier or sha-cache corruption.

Schema is versioned so we can evolve it without breaking older files. All
fields are optional — an empty file (or no file at all) means "inherit
everything from global". The merger in :func:`effective_settings` overlays
the per-app dict on top of a globals dict and returns a flat structure
plus a ``sources`` map so the UI can badge each value as
``global | app | default``.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional


APP_SETTINGS_FILENAME = "app_settings.json"
SCHEMA_VERSION = 1

# Whitelist of keys we recognise inside ``app_settings.json``. Anything else
# is preserved on read but ignored by the effective-merge so a typo can't
# silently change behaviour.
_KNOWN_TOP_LEVEL_KEYS = {
    "schema_version",
    "rag",
    "decompile",
    "inspect",
    "exploit",
    "chat",
    "hook",
    "tags",
    "notes",
}

# Per-section override keys we know how to merge. Anything else inside a
# section is preserved for forward-compat but not surfaced as an override.
_RAG_KEYS = {"embed_provider", "embed_model", "top_k_default"}
_DECOMPILE_KEYS = {"auto_rebuild_on_apk_change"}
_INSPECT_KEYS = {"default_logcat_buffer_lines", "logcat_kept_priorities"}
_EXPLOIT_KEYS = {"allow_destructive_actions", "verification_timeout_sec"}
_CHAT_KEYS = {"model_override", "temperature_override", "tab_overrides"}
# Hook Lab (4.5). ``hook_target_package_prefix`` is the allowlist the
# Inject route enforces — by default the app may only hook itself; an
# operator widens it explicitly when they need to hook a sibling
# package (e.g. a separate process). ``auto_attach_on_session_start``
# is read by 4.6's session-lifecycle owner; we surface it here so the
# Settings tab can write it once and 4.6 picks it up via the same
# read path.
_HOOK_KEYS = {"hook_target_package_prefix", "auto_attach_on_session_start"}


def app_settings_path(app_dir: Path) -> Path:
    """Path to ``apps/<app_id>/app_settings.json`` (does not create it)."""
    return Path(app_dir) / APP_SETTINGS_FILENAME


def default_app_settings() -> dict[str, Any]:
    """Empty per-app settings (everything inherits)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "rag": {},
        "decompile": {},
        "inspect": {},
        "exploit": {},
        "chat": {},
        "hook": {},
        "tags": [],
        "notes": "",
    }


def load_app_settings(app_dir: Path) -> dict[str, Any]:
    """Read ``app_settings.json`` (or return defaults on missing/invalid).

    Never raises — a corrupt file is treated like a missing file so the UI
    can recover by writing fresh defaults. The corrupt content is *not*
    deleted automatically (we don't want to lose user work silently).
    """
    p = app_settings_path(app_dir)
    if not p.is_file():
        return default_app_settings()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_app_settings()
    if not isinstance(data, dict):
        return default_app_settings()
    # Forward-compat: future schema versions are accepted but unknown
    # top-level keys are not silently merged into the effective view.
    out = default_app_settings()
    out.update({k: v for k, v in data.items() if k in _KNOWN_TOP_LEVEL_KEYS})
    # Make sure dict-shaped sections are dicts even if the file had ``null``.
    for sec in ("rag", "decompile", "inspect", "exploit", "chat", "hook"):
        if not isinstance(out.get(sec), dict):
            out[sec] = {}
    if not isinstance(out.get("tags"), list):
        out["tags"] = []
    if not isinstance(out.get("notes"), str):
        out["notes"] = ""
    out["schema_version"] = int(out.get("schema_version") or SCHEMA_VERSION)
    return out


def save_app_settings(app_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Atomically write ``app_settings.json`` and return the persisted dict.

    Uses ``tmp + os.replace`` so a crashed write never leaves a half-written
    file behind. Validates the schema version, preserves unknown top-level
    keys (for forward-compat) and normalises section types so subsequent
    reads don't have to re-validate.
    """
    p = app_settings_path(app_dir)
    p.parent.mkdir(parents=True, exist_ok=True)

    cleaned = default_app_settings()
    if isinstance(data, dict):
        for k, v in data.items():
            cleaned[k] = v
    cleaned["schema_version"] = SCHEMA_VERSION
    for sec in ("rag", "decompile", "inspect", "exploit", "chat", "hook"):
        if not isinstance(cleaned.get(sec), dict):
            cleaned[sec] = {}
    if not isinstance(cleaned.get("tags"), list):
        cleaned["tags"] = []
    cleaned["tags"] = [str(t).strip() for t in cleaned["tags"] if str(t).strip()]
    if not isinstance(cleaned.get("notes"), str):
        cleaned["notes"] = ""

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=".app_settings.", suffix=".json.tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2, sort_keys=True)
        os.replace(tmp_name, p)
    except OSError:
        # Best-effort cleanup; never raise from the rollback path.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return cleaned


def reset_app_settings(app_dir: Path) -> dict[str, Any]:
    """Restore defaults (everything inherits) and persist the empty file."""
    return save_app_settings(app_dir, default_app_settings())


# ---------------------------------------------------------------------------
# Override merging


def _pick(section_dict: dict[str, Any], key: str, allowed_keys: set[str]) -> Any:
    """Return the override for ``key`` if it's set + non-empty, else None.

    Empty strings, ``None``, and ``"inherit"`` are all treated as "no
    override" so the UI can use a single empty input to clear an override.
    """
    if key not in allowed_keys:
        return None
    if not isinstance(section_dict, dict):
        return None
    raw = section_dict.get(key)
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s.lower() == "inherit":
            return None
        return s
    return raw


def _global_value(global_view: dict[str, Any], section: str, key: str, fallback: Any = None) -> Any:
    """Look up a global value with optional fallback (None if missing)."""
    sec = global_view.get(section) or {}
    if not isinstance(sec, dict):
        return fallback
    val = sec.get(key)
    return val if val is not None else fallback


def effective_settings(
    *,
    global_view: dict[str, Any],
    per_app: dict[str, Any],
    app_package: Optional[str] = None,
) -> dict[str, Any]:
    """Merge ``per_app`` overrides on top of ``global_view``.

    ``global_view`` is the dict shape produced by
    :func:`androscan.config.loader.global_view_from_config` (a section ->
    flat-keys dict mirroring the YAML layout). ``app_package`` is the
    APK's manifest package id; it's used as the *default* fallback for
    the ``hook.hook_target_package_prefix`` allowlist (other sections
    don't need it). When omitted, the hook prefix surfaces with
    ``value=None`` and the route layer treats that as "operator hasn't
    set a prefix yet — block all packages".

    Returns a structure with one entry per logical setting:

    ::

        {
          "rag": {
            "embed_provider": {"value": "fastembed", "source": "global"},
            ...
          },
          ...
        }

    The frontend uses ``source`` to render the "global / app / default"
    pill next to each input.
    """
    out: dict[str, Any] = {}

    # ---- RAG -------------------------------------------------------------
    rag_overrides = per_app.get("rag") or {}
    out["rag"] = {}
    for k in _RAG_KEYS:
        ov = _pick(rag_overrides, k, _RAG_KEYS)
        if ov is not None:
            out["rag"][k] = {"value": ov, "source": "app"}
        else:
            out["rag"][k] = {
                "value": _global_value(global_view, "rag", k),
                "source": "global",
            }

    # ---- Decompile -------------------------------------------------------
    de_overrides = per_app.get("decompile") or {}
    out["decompile"] = {}
    for k in _DECOMPILE_KEYS:
        ov = _pick(de_overrides, k, _DECOMPILE_KEYS)
        out["decompile"][k] = (
            {"value": ov, "source": "app"} if ov is not None
            else {"value": _global_value(global_view, "decompile", k, True), "source": "default"}
        )

    # ---- Inspect ---------------------------------------------------------
    ins_overrides = per_app.get("inspect") or {}
    out["inspect"] = {}
    for k in _INSPECT_KEYS:
        ov = _pick(ins_overrides, k, _INSPECT_KEYS)
        if ov is not None:
            out["inspect"][k] = {"value": ov, "source": "app"}
        else:
            out["inspect"][k] = {
                "value": _global_value(global_view, "inspect", k),
                "source": "default",
            }

    # ---- Exploit ---------------------------------------------------------
    ex_overrides = per_app.get("exploit") or {}
    out["exploit"] = {}
    for k in _EXPLOIT_KEYS:
        ov = _pick(ex_overrides, k, _EXPLOIT_KEYS)
        if ov is not None:
            out["exploit"][k] = {"value": ov, "source": "app"}
        else:
            out["exploit"][k] = {
                "value": _global_value(global_view, "exploit", k),
                "source": "default",
            }

    # ---- Chat ------------------------------------------------------------
    ch_overrides = per_app.get("chat") or {}
    out["chat"] = {}
    for k in _CHAT_KEYS:
        ov = _pick(ch_overrides, k, _CHAT_KEYS)
        if ov is not None:
            out["chat"][k] = {"value": ov, "source": "app"}
        else:
            out["chat"][k] = {
                "value": _global_value(global_view, "chat", k),
                "source": "global",
            }

    # ---- Hook (4.5) -----------------------------------------------------
    # ``hook_target_package_prefix`` defaults to ``app_package`` (= the
    # app may only hook itself). ``auto_attach_on_session_start``
    # defaults to ``False`` — DEC-023 keeps Inject under explicit
    # operator confirmation, so opting in is a per-app decision.
    hk_overrides = per_app.get("hook") or {}
    out["hook"] = {}
    pref_ov = _pick(hk_overrides, "hook_target_package_prefix", _HOOK_KEYS)
    if pref_ov is not None:
        out["hook"]["hook_target_package_prefix"] = {"value": pref_ov, "source": "app"}
    else:
        out["hook"]["hook_target_package_prefix"] = {
            "value": app_package,
            "source": "default" if app_package else "default",
        }
    auto_raw = hk_overrides.get("auto_attach_on_session_start") if isinstance(hk_overrides, dict) else None
    if isinstance(auto_raw, bool):
        out["hook"]["auto_attach_on_session_start"] = {"value": auto_raw, "source": "app"}
    else:
        out["hook"]["auto_attach_on_session_start"] = {"value": False, "source": "default"}

    # ---- Free-form metadata ---------------------------------------------
    out["tags"] = list(per_app.get("tags") or [])
    out["notes"] = str(per_app.get("notes") or "")
    out["schema_version"] = int(per_app.get("schema_version") or SCHEMA_VERSION)
    return out


def overrides_for_runtime(per_app: dict[str, Any]) -> dict[str, Any]:
    """Project ``per_app`` to a flat dict mirroring ``Config`` field names.

    Used by code paths that already have a global ``Config`` and want to
    apply per-app overrides locally without round-tripping through the
    effective-settings dict shape. Only includes keys that map cleanly to
    ``Config`` attributes.
    """
    rag = per_app.get("rag") or {}
    out: dict[str, Any] = {}
    if "embed_provider" in rag and rag["embed_provider"]:
        out["rag_embed_provider"] = str(rag["embed_provider"]).strip()
    if "embed_model" in rag and rag["embed_model"]:
        out["rag_embed_model"] = str(rag["embed_model"]).strip()
    if "top_k_default" in rag and rag["top_k_default"] not in (None, ""):
        try:
            out["rag_top_k_default"] = max(1, int(rag["top_k_default"]))
        except (TypeError, ValueError):
            pass
    chat = per_app.get("chat") or {}
    if chat.get("model_override"):
        out["ollama_model"] = str(chat["model_override"]).strip()
    if chat.get("temperature_override") is not None:
        try:
            out["ollama_temperature"] = float(chat["temperature_override"])
        except (TypeError, ValueError):
            pass
    return out


def merge_for_app(
    global_dict: dict[str, Any],
    per_app: dict[str, Any],
) -> dict[str, Any]:
    """Return a flat, runtime-ready dict combining global + per-app overrides.

    ``global_dict`` is expected to be the keyword form of ``Config``
    (i.e. ``dataclasses.asdict(config)``). Used by callers that need a
    per-tap effective config — most internal code reads from the
    process-wide ``Config`` directly, but Inspect-tab features that want
    to honour per-app RAG provider switches will use this.
    """
    merged = deepcopy(global_dict) if global_dict else {}
    merged.update(overrides_for_runtime(per_app))
    return merged


def apk_overrides_summary(per_app: dict[str, Any]) -> list[str]:
    """Return one human-readable line per active override.

    Used to surface "the things this app overrides" in the Settings UI
    sidebar without forcing the user to expand every section.
    """
    out: list[str] = []
    rag = per_app.get("rag") or {}
    for k in _RAG_KEYS:
        if rag.get(k):
            out.append(f"rag.{k} = {rag[k]!r}")
    chat = per_app.get("chat") or {}
    if chat.get("model_override"):
        out.append(f"chat.model = {chat['model_override']!r}")
    if chat.get("temperature_override") is not None:
        out.append(f"chat.temperature = {chat['temperature_override']!r}")
    hook = per_app.get("hook") or {}
    if isinstance(hook, dict):
        prefix = hook.get("hook_target_package_prefix")
        if isinstance(prefix, str) and prefix.strip():
            out.append(f"hook.target_prefix = {prefix.strip()!r}")
        auto = hook.get("auto_attach_on_session_start")
        if isinstance(auto, bool) and auto:
            out.append("hook.auto_attach_on_session_start = True")
    if per_app.get("tags"):
        out.append(f"tags = {', '.join(per_app['tags'])}")
    return out


def _validate_hook_patch(patch: dict[str, Any]) -> Optional[str]:
    """Validate a partial update to the ``hook`` section.

    ``hook_target_package_prefix`` must be a non-empty string (or
    ``None`` to clear the override); ``auto_attach_on_session_start``
    must be a bool. Anything else is left to the forward-compat merge —
    we deliberately don't reject unknown sub-keys here because
    schema-version bumps will introduce more knobs and we don't want
    older servers refusing newer clients' patches.
    """
    if "hook_target_package_prefix" in patch:
        v = patch["hook_target_package_prefix"]
        if v is None:
            pass  # clearing the override is allowed
        elif not isinstance(v, str):
            return "'hook.hook_target_package_prefix' must be a string or null"
        elif not v.strip():
            return "'hook.hook_target_package_prefix' must be non-empty (use null to clear)"
        elif len(v) > 255:
            return "'hook.hook_target_package_prefix' too long (max 255 chars)"
    if "auto_attach_on_session_start" in patch:
        v = patch["auto_attach_on_session_start"]
        if v is not None and not isinstance(v, bool):
            return "'hook.auto_attach_on_session_start' must be a boolean or null"
    return None


def coerce_partial_update(
    existing: dict[str, Any],
    patch: dict[str, Any],
) -> tuple[dict[str, Any], Optional[str]]:
    """Apply a partial update to ``existing``. Returns ``(new_dict, error)``.

    Validates types so the HTTP layer can return a 400 with a clear field
    name on bad input. The frontend uses this contract to render
    field-level error pills.
    """
    if not isinstance(patch, dict):
        return existing, "patch must be an object"
    out = deepcopy(existing)
    for k, v in patch.items():
        if k not in _KNOWN_TOP_LEVEL_KEYS and k != "schema_version":
            return existing, f"unknown top-level key: {k!r}"
        if k in ("rag", "decompile", "inspect", "exploit", "chat", "hook"):
            if v is None:
                out[k] = {}
                continue
            if not isinstance(v, dict):
                return existing, f"{k!r} must be an object or null"
            # ``hook`` is the first section with strongly-typed fields
            # we want to fail fast on (a malformed prefix would let
            # the Inject route happily accept the wrong package). The
            # other sections still flow through the generic merge so
            # forward-compat keys aren't rejected.
            if k == "hook":
                err = _validate_hook_patch(v)
                if err is not None:
                    return existing, err
            section = dict(out.get(k) or {})
            for sk, sv in v.items():
                section[sk] = sv
            out[k] = section
        elif k == "tags":
            if v is None:
                out["tags"] = []
                continue
            if not isinstance(v, list):
                return existing, "'tags' must be a list of strings or null"
            out["tags"] = [str(t).strip() for t in v if str(t).strip()]
        elif k == "notes":
            if v is None:
                out["notes"] = ""
            elif not isinstance(v, str):
                return existing, "'notes' must be a string or null"
            else:
                if len(v) > 8000:
                    return existing, "'notes' too long (max 8000 chars)"
                out["notes"] = v
        elif k == "schema_version":
            try:
                out["schema_version"] = int(v)
            except (TypeError, ValueError):
                return existing, "'schema_version' must be an integer"
    return out, None
