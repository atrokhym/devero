"""Versioned persistence helpers for OASIS verifier audit artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import tempfile
from typing import Any, Dict, Optional, Tuple


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]", "_", value or "").strip("_")
    return cleaned or "unknown"


def _atomic_json_write(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=".audit_",
        suffix=".tmp",
        dir=os.path.dirname(path),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def save_audit_run(
    log_dir: str,
    patient: str,
    target_date: str,
    payload: Dict[str, Any],
    now: Optional[datetime] = None,
    filename_prefix: str = "audit",
) -> Tuple[str, str]:
    """Write one immutable run plus the backward-compatible latest snapshot."""
    created_at = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    run_id = created_at.strftime("%Y%m%dT%H%M%S%fZ")

    patient_key = _safe_component(patient)
    date_key = re.sub(r"[^0-9]", "", target_date or "") or _safe_component(target_date)
    prefix_key = _safe_component(filename_prefix)
    base_name = f"{prefix_key}_{patient_key}_{date_key}"
    run_path = os.path.join(log_dir, f"{base_name}_{run_id}.json")
    latest_path = os.path.join(log_dir, f"{base_name}.json")

    persisted = dict(payload)
    persisted["audit_run"] = {
        "schema_version": 2,
        "run_id": run_id,
        "created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
        "immutable_run_file": os.path.basename(run_path),
        "latest_snapshot_file": os.path.basename(latest_path),
    }

    _atomic_json_write(run_path, persisted)
    _atomic_json_write(latest_path, persisted)
    return run_path, latest_path
