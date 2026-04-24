"""Per-finding triage decisions stored at apps/<app>/<run>/triage.json.

The file is a JSON object: ``{ "<finding_id>": TriageEntry, ... }``. We never
mutate ``report.json`` itself — UI badges/severity overrides are derived by
merging this map at read time.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from androscan.web.paths import safe_child

TRIAGE_FILENAME = "triage.json"
ALLOWED_STATUSES = {"confirmed", "false_positive", "suppressed", "needs_review"}
ALLOWED_SEVERITY_OVERRIDES = {None, "critical", "high", "medium", "low", "informational"}
NOTE_MAX = 2000


def triage_path(root: Path, app_id: str, run_ts: str) -> Optional[Path]:
    """Return the path to triage.json for ``app_id/run_ts``, or None if unsafe."""
    return safe_child(root, app_id, run_ts, TRIAGE_FILENAME)


def load_triage(root: Path, app_id: str, run_ts: str) -> dict[str, dict[str, Any]]:
    """Read triage map (empty dict if file is missing/corrupt)."""
    p = triage_path(root, app_id, run_ts)
    if p is None or not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def upsert_triage(
    root: Path,
    app_id: str,
    run_ts: str,
    finding_id: str,
    *,
    status: Optional[str] = None,
    severity_override: Optional[str] = None,
    note: Optional[str] = None,
    actor: str = "user",
) -> tuple[bool, str, dict[str, Any]]:
    """Upsert one finding's triage entry. Returns (ok, error, entry)."""
    if not finding_id or not finding_id.strip():
        return False, "finding_id is required", {}
    finding_id = finding_id.strip()
    if status is not None and status not in ALLOWED_STATUSES:
        return False, f"status must be one of {sorted(ALLOWED_STATUSES)}", {}
    sev_norm: Optional[str] = None
    if severity_override is not None:
        sev_norm = severity_override.strip().lower() or None
    if sev_norm not in ALLOWED_SEVERITY_OVERRIDES:
        return False, f"severity_override must be one of {sorted(s for s in ALLOWED_SEVERITY_OVERRIDES if s)} or null", {}
    if note is not None:
        note = str(note)
        if len(note) > NOTE_MAX:
            return False, f"note too long (max {NOTE_MAX} chars)", {}

    run_dir = safe_child(root, app_id, run_ts)
    if run_dir is None or not run_dir.is_dir():
        return False, "unknown run", {}
    p = triage_path(root, app_id, run_ts)
    assert p is not None

    existing = load_triage(root, app_id, run_ts)
    prior = existing.get(finding_id, {})
    entry: dict[str, Any] = dict(prior)
    if status is not None:
        entry["status"] = status
    if severity_override is not None:
        entry["severity_override"] = sev_norm
    if note is not None:
        entry["note"] = note
    entry["finding_id"] = finding_id
    entry["actor"] = actor
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    existing[finding_id] = entry
    try:
        p.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as e:
        return False, f"failed to write triage.json: {e}", {}
    return True, "", entry
