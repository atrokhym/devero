"""Run an existing Devero copier from an opaque, authorized case id."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .browser_lease import BrowserClaimManager, BrowserLease
from .case_store import CaseStore

WORKSPACE = Path(__file__).resolve().parent.parent


def _script_for(identity: dict) -> Path:
    names = f"{identity.get('doc_type', '')} {identity.get('source_doc_type', '')}".lower()
    if any(token in names for token in ("oasis", "resumption of care", "start of care")):
        return WORKSPACE / "devero_copier_oasis.py"
    return WORKSPACE / "devero_copier_note.py"


def run_copy(case_id: str, store: CaseStore | None = None) -> dict:
    store = store or CaseStore()
    record = store.load(case_id)
    store.consume_save_authorization(case_id, record["identity_fingerprint"])
    script = _script_for(record["identity"])
    if not script.exists():
        raise RuntimeError("Approved copier is unavailable")
    task_path = store.root / f".{case_id}.task.json"
    log_path = store.root / f".{case_id}.copier.log"
    task_path.write_text(__import__("json").dumps([record["identity"]]), encoding="utf-8")
    os.chmod(task_path, 0o600)
    claims = BrowserClaimManager(store)
    claims.claim(case_id, "copier")
    try:
        with BrowserLease(case_id, "copier", store=store, timeout_seconds=1):
            env = os.environ.copy()
            env["DEVERO_KEEP_BROWSER_OPEN"] = "true"
            env["DEVERO_LOG_FILE"] = str(log_path)
            completed = subprocess.run(
                [sys.executable, str(script), str(task_path)],
                cwd=WORKSPACE,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=1800,
                check=False,
            )
        output = completed.stdout or ""
        succeeded = completed.returncode == 0 and (
            "[SUCCESS] Transfer completed" in output or "[SUCCEEDED]" in output
        )
        status = "copied" if succeeded else "copy_failed"
        counts = {"updated": 0, "preserved": 0}
        if succeeded:
            import re

            updated = re.search(r"(?:Target Fields Populated|Populated):\s*(\d+)", output)
            preserved = re.search(r"(?:Pre-filled Fields Preserved|Preserved):\s*(\d+)", output)
            counts = {
                "updated": int(updated.group(1)) if updated else 0,
                "preserved": int(preserved.group(1)) if preserved else 0,
            }

        def apply(current: dict) -> None:
            current["status"] = status
            current["transfer"] = {
                **counts,
                "returncode": completed.returncode,
                "completed_at": time.time(),
                "diagnostic": output[-20000:],
            }

        store.mutate(case_id, apply)
        if not succeeded:
            raise RuntimeError("Copy failed; inspect the secure case diagnostic")
        return {"case_id": case_id, "status": status, **counts}
    finally:
        claims.release(case_id, "copier")
        task_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Run an authorized Devero copy by opaque case id")
    parser.add_argument("case_id")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_copy(args.case_id), sort_keys=True))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
