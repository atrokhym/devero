"""Cross-process lease for the single shared Devero Chrome session."""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from .case_store import CASE_ID_RE, CaseStore, CaseStoreError
from .policy import normalize_role


class BrowserBusy(CaseStoreError):
    pass


class BrowserClaimManager:
    """A task-level ownership claim used by Bots around browser-tool calls.

    The OS flock below protects deterministic copier processes.  This claim
    spans multiple Bot tool calls, so Chief can serialize Writer/Verifier work
    that uses Hermes's existing Chrome DevTools MCP.
    """

    def __init__(self, store: CaseStore | None = None, ttl_seconds: int = 15 * 60):
        self.store = store or CaseStore()
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.path = self.store.root / ".browser-claim.json"

    def _read(self) -> dict[str, Any] | None:
        try:
            if self.path.is_symlink():
                return None
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if float(data.get("expires_at", 0)) <= time.time():
                self.path.unlink(missing_ok=True)
                return None
            return data
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            return None

    def claim(self, case_id: str, role: str) -> dict[str, Any]:
        self.store.load(case_id)
        role = normalize_role(role)
        with self.store._store_lock():
            existing = self._read()
            if existing and (existing.get("case_id"), existing.get("role")) != (case_id, role):
                raise BrowserBusy("The shared Devero browser is owned by another active task")
            claim = existing or {
                "case_id": case_id,
                "role": role,
                "token": secrets.token_hex(16),
                "claimed_at": time.time(),
            }
            claim["expires_at"] = time.time() + self.ttl_seconds
            fd = os.open(self.path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, (json.dumps(claim, sort_keys=True) + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            return {key: claim[key] for key in ("case_id", "role", "claimed_at", "expires_at")}

    def heartbeat(self, case_id: str, role: str) -> dict[str, Any]:
        role = normalize_role(role)
        with self.store._store_lock():
            existing = self._read()
            if not existing or (existing.get("case_id"), existing.get("role")) != (case_id, role):
                raise BrowserBusy("This task does not own the shared Devero browser")
            existing["expires_at"] = time.time() + self.ttl_seconds
            fd = os.open(self.path, os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW)
            try:
                os.write(fd, (json.dumps(existing, sort_keys=True) + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            return {key: existing[key] for key in ("case_id", "role", "claimed_at", "expires_at")}

    def release(self, case_id: str, role: str) -> None:
        role = normalize_role(role)
        with self.store._store_lock():
            existing = self._read()
            if not existing:
                return
            if (existing.get("case_id"), existing.get("role")) != (case_id, role):
                raise BrowserBusy("This task does not own the shared Devero browser")
            self.path.unlink(missing_ok=True)

    def status(self) -> dict[str, Any]:
        with self.store._store_lock():
            existing = self._read()
            if not existing:
                return {"busy": False}
            return {
                "busy": True,
                "case_id": existing["case_id"],
                "role": existing["role"],
                "expires_at": existing["expires_at"],
            }


class BrowserLease:
    def __init__(
        self,
        case_id: str,
        role: str,
        *,
        store: CaseStore | None = None,
        timeout_seconds: float = 0,
    ) -> None:
        if not CASE_ID_RE.fullmatch(case_id):
            raise ValueError("Invalid case id")
        self.case_id = case_id
        self.role = normalize_role(role)
        self.store = store or CaseStore()
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.path = self.store.root / ".browser.lock"
        self._fd: int | None = None

    def acquire(self) -> "BrowserLease":
        if self._fd is not None:
            return self
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise BrowserBusy("The shared Devero browser is busy")
                time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))
        metadata: dict[str, Any] = {
            "case_id": self.case_id,
            "role": self.role,
            "pid": os.getpid(),
            "started_at": time.time(),
        }
        os.ftruncate(fd, 0)
        os.write(fd, (json.dumps(metadata, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(fd)
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        os.ftruncate(self._fd, 0)
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None

    def __enter__(self) -> "BrowserLease":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
