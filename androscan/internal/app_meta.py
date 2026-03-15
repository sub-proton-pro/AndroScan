"""App-level metadata at apps/<app_id>/app_meta.json. Reuse dossier when APK hash matches."""

import hashlib
import json
from pathlib import Path
from typing import Any, Optional


APP_META_FILENAME = "app_meta.json"
EXTRACTED_APK_DIR = "extracted_apk"


def compute_apk_sha256(apk_path: str | Path) -> str:
    """SHA-256 hash of the APK file (chunked read)."""
    path = Path(apk_path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _app_meta_path(app_id_root: Path) -> Path:
    return Path(app_id_root) / APP_META_FILENAME


def extracted_apk_path(app_id_root: Path) -> Path:
    """Path to the persistent extracted APK directory for this app."""
    return Path(app_id_root) / EXTRACTED_APK_DIR


def load_app_meta(app_id_root: Path) -> Optional[dict[str, Any]]:
    """Load app_meta.json if present. Returns dict with apk_sha256, dossier, etc. or None."""
    path = _app_meta_path(app_id_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("apk_sha256") and "dossier" in data:
            return data
        return None
    except (json.JSONDecodeError, OSError):
        return None


def save_app_meta(
    app_id_root: Path,
    apk_sha256: str,
    dossier: dict[str, Any],
    apk_path: Optional[str] = None,
) -> None:
    """Write app_meta.json with apk_sha256, dossier, and optional apk_path."""
    path = _app_meta_path(app_id_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "apk_sha256": apk_sha256,
        "dossier": dossier,
    }
    if apk_path is not None:
        payload["apk_path"] = apk_path
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
