"""Short-lived, owner-private storage for Devero case identity and PHI.

Hermes conversations and Kanban cards use only the opaque case id.  Patient
identity and clinical values remain in these TTL-limited files.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

CASE_ID_RE = re.compile(r"^DV-[A-F0-9]{12}$")
ARTIFACT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 4 * 60 * 60
TERMINAL_STATES = {"closed", "cancelled", "expired"}


class CaseStoreError(RuntimeError):
    """Base class for safe, non-PHI case-store failures."""


class CaseNotFound(CaseStoreError):
    pass


class CaseExpired(CaseStoreError):
    pass


class SaveNotAuthorized(CaseStoreError):
    pass


def _runtime_root() -> Path:
    explicit = os.getenv("DEVERO_FLEET_RUNTIME_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.getenv("XDG_RUNTIME_DIR", "").strip()
    if xdg:
        return Path(xdg) / "devero-fleet"
    return Path(tempfile.gettempdir()) / f"devero-fleet-{os.getuid()}"


def _now() -> float:
    return time.time()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class CaseStore:
    def __init__(self, root: Path | str | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.root = Path(root) if root is not None else _runtime_root()
        self.ttl_seconds = int(ttl_seconds)
        if self.ttl_seconds < 60:
            raise ValueError("Case TTL must be at least 60 seconds")
        self._ensure_root()

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise CaseStoreError("Runtime directory must not be a symlink")
        mode = stat.S_IMODE(self.root.stat().st_mode)
        if mode & 0o077:
            os.chmod(self.root, 0o700)

    def _path(self, case_id: str) -> Path:
        if not CASE_ID_RE.fullmatch(case_id):
            raise CaseNotFound("Invalid case id")
        return self.root / f"{case_id}.json"

    def _artifact_path(self, case_id: str, name: str) -> Path:
        self._path(case_id)
        if not ARTIFACT_NAME_RE.fullmatch(name):
            raise CaseStoreError("Invalid artifact name")
        return self.root / f".{case_id}.{name}.json"

    @contextlib.contextmanager
    def _store_lock(self) -> Iterator[None]:
        lock_path = self.root / ".cases.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_unlocked(self, case_id: str, allow_expired: bool = False) -> dict[str, Any]:
        path = self._path(case_id)
        try:
            if path.is_symlink():
                raise CaseNotFound("Unsafe case path")
            with path.open("r", encoding="utf-8") as handle:
                record = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise CaseNotFound("Case not found") from exc
        if record.get("case_id") != case_id or record.get("schema_version") != SCHEMA_VERSION:
            raise CaseNotFound("Case record is invalid")
        if not allow_expired and float(record.get("expires_at", 0)) <= _now():
            raise CaseExpired("Case expired")
        return record

    def _write_unlocked(self, record: dict[str, Any]) -> None:
        target = self._path(record["case_id"])
        fd, tmp_name = tempfile.mkstemp(prefix=".case-", suffix=".tmp", dir=self.root)
        try:
            os.fchmod(fd, 0o600)
            payload = (_canonical_json(record) + "\n").encode("utf-8")
            os.write(fd, payload)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(tmp_name, target)
            os.chmod(target, 0o600)
        finally:
            if fd >= 0:
                os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)

    @staticmethod
    def identity_fingerprint(identity: dict[str, Any], nonce: str) -> str:
        normalized = {
            key: " ".join(str(value or "").strip().lower().split())
            for key, value in sorted(identity.items())
            if key not in {"notes", "raw_status"}
        }
        payload = f"{nonce}:{_canonical_json(normalized)}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def create(self, *, identity: dict[str, Any], mode: str, raw_status: str = "") -> str:
        required = ("patient", "doc_type", "target_date")
        if any(not str(identity.get(key, "")).strip() for key in required):
            raise ValueError("patient, doc_type, and target_date are required")
        if mode not in {"copy", "write", "verify", "correct"}:
            raise ValueError("mode must be copy, write, verify, or correct")
        now = _now()
        with self._store_lock():
            while True:
                case_id = f"DV-{secrets.token_hex(6).upper()}"
                if not self._path(case_id).exists():
                    break
            nonce = secrets.token_hex(16)
            record: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "case_id": case_id,
                "created_at": now,
                "updated_at": now,
                "expires_at": now + self.ttl_seconds,
                "status": "intake",
                "mode": mode,
                "identity": dict(identity),
                "raw_status": raw_status,
                "nonce": nonce,
                "identity_fingerprint": self.identity_fingerprint(identity, nonce),
                "resolution": {},
                "authorization": {"save": False},
                "punch_list": [],
                "corrections": [],
                "verification": {},
                "transfer": {},
                "events": [],
            }
            self._write_unlocked(record)
        return case_id

    def load(self, case_id: str, *, allow_expired: bool = False) -> dict[str, Any]:
        with self._store_lock():
            return self._read_unlocked(case_id, allow_expired=allow_expired)

    def write_artifact(self, case_id: str, name: str, payload: Any) -> Path:
        """Atomically store PHI-bearing evidence beside the expiring case."""
        with self._store_lock():
            self._read_unlocked(case_id)
            target = self._artifact_path(case_id, name)
            fd, temporary = tempfile.mkstemp(prefix=".evidence-", suffix=".tmp", dir=self.root)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, (_canonical_json(payload) + "\n").encode("utf-8"))
                os.fsync(fd)
                os.close(fd)
                fd = -1
                os.replace(temporary, target)
                os.chmod(target, 0o600)
            finally:
                if fd >= 0:
                    os.close(fd)
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temporary)
            return target

    def read_artifact(self, case_id: str, name: str) -> Any:
        with self._store_lock():
            self._read_unlocked(case_id)
            path = self._artifact_path(case_id, name)
            try:
                if path.is_symlink():
                    raise CaseStoreError("Unsafe artifact path")
                return json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                raise CaseStoreError("Verification artifact not found") from exc

    def mutate(self, case_id: str, mutator) -> dict[str, Any]:
        with self._store_lock():
            record = self._read_unlocked(case_id)
            if record.get("status") in TERMINAL_STATES:
                raise CaseStoreError("Case is no longer active")
            mutator(record)
            record["updated_at"] = _now()
            self._write_unlocked(record)
            return record

    def summary(self, case_id: str) -> dict[str, Any]:
        record = self.load(case_id)
        resolution = record.get("resolution") or {}
        authorization = record.get("authorization") or {}
        return {
            "case_id": case_id,
            "mode": record["mode"],
            "status": record["status"],
            "resolved": bool(resolution.get("confirmed")),
            "save_authorized": bool(authorization.get("save")),
            "punch_count": len(record.get("punch_list") or []),
            "approved_correction_count": sum(
                1 for item in record.get("corrections") or [] if item.get("approved")
            ),
            "expires_at": record["expires_at"],
        }

    def work_context(self, case_id: str, role: str) -> dict[str, Any]:
        """Return the minimum PHI-bearing context needed by one specialist.

        This output is for the specialist's local tool context only. It must
        never be copied into Bot messages, Kanban, memories, or reports.
        """
        from .policy import normalize_role

        role = normalize_role(role)
        if role == "chief":
            raise CaseStoreError("Chief cannot read PHI-bearing work context")
        record = self.load(case_id)
        context: dict[str, Any] = {
            "case_id": case_id,
            "mode": record["mode"],
            "identity": record["identity"],
        }
        if role == "writer":
            context["raw_status"] = record.get("raw_status", "")
            context["corrections"] = [
                item for item in record.get("corrections") or [] if item.get("approved")
            ]
            context["punch_list"] = record.get("punch_list") or []
        elif role == "verifier":
            context["raw_status"] = record.get("raw_status", "")
            context["transfer"] = record.get("transfer") or {}
            context["corrections"] = record.get("corrections") or []
            context["verification"] = record.get("verification") or {}
        return context

    def mark_resolved(self, case_id: str, *, live_fingerprint: str, facts: dict[str, Any]) -> dict[str, Any]:
        def apply(record: dict[str, Any]) -> None:
            expected = record["identity_fingerprint"]
            confirmed = secrets.compare_digest(expected, live_fingerprint)
            record["resolution"] = {
                "confirmed": confirmed,
                "checked_at": _now(),
                "live_fingerprint": live_fingerprint,
                "facts": facts,
            }
            record["authorization"] = {"save": False}
            record["status"] = "resolved" if confirmed else "blocked"
            record["events"].append({"at": _now(), "type": "identity_resolved", "confirmed": confirmed})

        return self.mutate(case_id, apply)

    def authorize_save(self, case_id: str) -> dict[str, Any]:
        def apply(record: dict[str, Any]) -> None:
            resolution = record.get("resolution") or {}
            if not resolution.get("confirmed"):
                raise SaveNotAuthorized("Live target identity is not confirmed")
            if not secrets.compare_digest(
                record["identity_fingerprint"], str(resolution.get("live_fingerprint", ""))
            ):
                raise SaveNotAuthorized("Resolved target no longer matches the case")
            record["authorization"] = {
                "save": True,
                "authorized_at": _now(),
                "fingerprint": resolution["live_fingerprint"],
            }
            record["status"] = "authorized"
            record["events"].append({"at": _now(), "type": "save_authorized"})

        return self.mutate(case_id, apply)

    def require_save_authorization(self, case_id: str, live_fingerprint: str) -> dict[str, Any]:
        record = self.load(case_id)
        self._validate_save_authorization(record, live_fingerprint)
        return record

    @staticmethod
    def _validate_save_authorization(record: dict[str, Any], live_fingerprint: str) -> None:
        auth = record.get("authorization") or {}
        if not auth.get("save") or auth.get("consumed"):
            raise SaveNotAuthorized("Save is not authorized for this case")
        expected = str(auth.get("fingerprint", ""))
        if not expected or not secrets.compare_digest(expected, live_fingerprint):
            raise SaveNotAuthorized("Save authorization does not match the live target")

    def consume_save_authorization(self, case_id: str, live_fingerprint: str) -> dict[str, Any]:
        """Consume Chief's one-use Save grant immediately before a Save action."""

        def apply(record: dict[str, Any]) -> None:
            self._validate_save_authorization(record, live_fingerprint)
            record["authorization"]["consumed"] = True
            record["authorization"]["consumed_at"] = _now()
            record["status"] = "saving"
            record["events"].append({"at": _now(), "type": "save_authorization_consumed"})

        return self.mutate(case_id, apply)

    def approve_corrections(self, case_id: str, correction_ids: list[str]) -> dict[str, Any]:
        wanted = set(correction_ids)

        def apply(record: dict[str, Any]) -> None:
            known = {str(item.get("id")) for item in record.get("corrections") or []}
            unknown = wanted - known
            if unknown:
                raise CaseStoreError("Unknown correction id")
            for item in record.get("corrections") or []:
                if str(item.get("id")) in wanted:
                    item["approved"] = True
            record["events"].append({"at": _now(), "type": "corrections_approved", "count": len(wanted)})

        return self.mutate(case_id, apply)

    def record_verification(
        self,
        case_id: str,
        result: dict[str, Any],
        *,
        required_corrections: list[dict[str, Any]] | None = None,
        require_final: bool = False,
    ) -> dict[str, Any]:
        from .verification import validate_result

        validated = validate_result(result)
        if require_final and (
            validated.get("review_stage") != "FINAL"
            or validated.get("semantic_review_required") is not False
        ):
            raise CaseStoreError("Recorded AI review must be explicitly FINAL")
        if required_corrections is not None:
            immutable = (
                "id", "issue_class", "location", "observed_issue", "expected_correction",
                "evidence_class", "source_class", "fact_status", "route",
            )
            submitted = {str(item.get("id")): item for item in validated.get("corrections") or []}
            for required in required_corrections:
                candidate = submitted.get(str(required.get("id")))
                if candidate is None or any(candidate.get(key) != required.get(key) for key in immutable):
                    raise CaseStoreError("Deterministic verification findings must be preserved unchanged")

        def apply(record: dict[str, Any]) -> None:
            previous = {str(item.get("id")): item for item in record.get("corrections") or []}
            corrections = []
            for item in validated.get("corrections") or []:
                old = previous.get(str(item.get("id")))
                unchanged = old and all(
                    old.get(key) == item.get(key)
                    for key in ("location", "observed_issue", "expected_correction")
                )
                item = dict(item)
                item["approved"] = bool(unchanged and old.get("approved"))
                corrections.append(item)
            record["corrections"] = corrections
            record["verification"] = {
                key: value
                for key, value in validated.items()
                if key not in {"corrections", "snapshot", "cross_version"}
            }
            if validated.get("run_status") != "COMPLETE":
                record["status"] = "blocked"
            elif validated.get("semantic_review_required"):
                record["status"] = "verification_pending"
            else:
                record["status"] = "verified"
            record["events"].append(
                {
                    "at": _now(),
                    "type": "verification_recorded",
                    "verdict": validated.get("verdict"),
                    "correction_count": len(corrections),
                }
            )

        return self.mutate(case_id, apply)

    def update_section(self, case_id: str, section: str, value: Any, *, status: str | None = None) -> dict[str, Any]:
        allowed = {"punch_list", "corrections", "verification", "transfer", "resolution"}
        if section not in allowed:
            raise ValueError("Unsupported case section")

        def apply(record: dict[str, Any]) -> None:
            record[section] = value
            if status:
                record["status"] = status

        return self.mutate(case_id, apply)

    def cancel(self, case_id: str) -> None:
        self._delete(case_id, terminal_state="cancelled")

    def close(self, case_id: str) -> None:
        self._delete(case_id, terminal_state="closed")

    def _delete(self, case_id: str, terminal_state: str) -> None:
        with self._store_lock():
            path = self._path(case_id)
            if not path.exists():
                raise CaseNotFound("Case not found")
            # Terminal state intentionally exists only in memory; PHI is removed
            # from disk instead of retaining a tombstone beside the case id.
            path.unlink()
            for auxiliary in self.root.glob(f".{case_id}.*"):
                if not auxiliary.is_symlink():
                    auxiliary.unlink(missing_ok=True)
            self._drop_case_browser_claim_unlocked(case_id)

    def _drop_case_browser_claim_unlocked(self, case_id: str) -> None:
        claim_path = self.root / ".browser-claim.json"
        try:
            if claim_path.is_symlink():
                return
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
            if claim.get("case_id") == case_id:
                claim_path.unlink(missing_ok=True)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return

    def sweep_expired(self, now: float | None = None) -> int:
        cutoff = _now() if now is None else float(now)
        removed = 0
        with self._store_lock():
            for path in self.root.glob("DV-*.json"):
                if path.is_symlink():
                    continue
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        record = json.load(handle)
                    if float(record.get("expires_at", 0)) <= cutoff:
                        path.unlink()
                        case_id = str(record.get("case_id", ""))
                        if CASE_ID_RE.fullmatch(case_id):
                            for auxiliary in self.root.glob(f".{case_id}.*"):
                                if not auxiliary.is_symlink():
                                    auxiliary.unlink(missing_ok=True)
                            self._drop_case_browser_claim_unlocked(case_id)
                        removed += 1
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
        return removed
