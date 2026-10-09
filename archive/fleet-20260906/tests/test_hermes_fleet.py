from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from devero_fleet import case_store
from devero_fleet.browser_lease import BrowserBusy, BrowserClaimManager
from devero_fleet.case_store import CaseStore, SaveNotAuthorized
from devero_fleet.cli import _intake_payload
from devero_fleet.copier_runner import _script_for
from devero_fleet.policy import ROLE_POLICIES, allowed_actions
from devero_fleet.workflow import after_verification, after_write, initial_route, sanitize_summary

import scripts.setup_hermes_fleet as fleet_setup


IDENTITY = {
    "patient": "Fixture, Private",
    "caregiver": "Fixture Nurse",
    "doc_type": "Skilled Nurse Visit Note",
    "source_doc_type": "Skilled Nurse Visit Note",
    "source_date": "01/01/2000",
    "target_date": "01/08/2000",
}


def make_store(tmp_path: Path) -> CaseStore:
    return CaseStore(root=tmp_path / "runtime", ttl_seconds=300)


def create_case(store: CaseStore, mode: str = "copy") -> str:
    return store.create(identity=IDENTITY, mode=mode, raw_status="fixture clinical status")


def attest(store: CaseStore, case_id: str) -> dict:
    record = store.load(case_id)
    return store.mark_resolved(
        case_id,
        live_fingerprint=record["identity_fingerprint"],
        facts={"patient_match": True, "document_match": True, "date_match": True, "pending_target": True},
    )


def test_case_files_are_private_and_summary_is_phi_minimized(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    case_id = create_case(store)
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE((store.root / f"{case_id}.json").stat().st_mode) == 0o600
    summary = json.dumps(store.summary(case_id))
    assert "Fixture, Private" not in summary
    assert "Fixture Nurse" not in summary
    assert "fixture clinical status" not in summary
    assert "01/08/2000" not in summary
    assert "Skilled Nurse Visit Note" not in summary
    assert case_id in summary


def test_file_intake_requires_owner_private_permissions(tmp_path: Path) -> None:
    source = tmp_path / "intake.json"
    source.write_text(json.dumps({"mode": "copy", "identity": IDENTITY}), encoding="utf-8")
    source.chmod(0o644)
    with pytest.raises(ValueError, match="owner-private"):
        _intake_payload(str(source))
    source.chmod(0o600)
    assert _intake_payload(str(source))["mode"] == "copy"


def test_chief_telegram_approvers_exclude_bot_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "hermes"
    chief = home / "profiles" / "devero"
    chief.mkdir(parents=True)
    (home / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=100000:default-token\n"
        "TELEGRAM_ALLOWED_USERS=100000,200000,300000\n",
        encoding="utf-8",
    )
    (chief / ".env").write_text("TELEGRAM_BOT_TOKEN=300000:chief-token\n", encoding="utf-8")
    monkeypatch.setattr(fleet_setup, "HERMES_HOME", home)
    fleet_setup._ensure_chief_telegram_approvers(chief)
    values = fleet_setup._read_env(chief / ".env")
    assert values["TELEGRAM_ALLOWED_USERS"] == "200000"
    assert stat.S_IMODE((chief / ".env").stat().st_mode) == 0o600


def test_work_context_is_role_minimized(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    case_id = create_case(store)
    store.update_section(
        case_id,
        "corrections",
        [{"id": "C1", "approved": True}, {"id": "C2", "approved": False}],
    )
    copier = store.work_context(case_id, "copier")
    writer = store.work_context(case_id, "writer")
    verifier = store.work_context(case_id, "verifier")
    assert set(copier) == {"case_id", "mode", "identity"}
    assert [item["id"] for item in writer["corrections"]] == ["C1"]
    assert {item["id"] for item in verifier["corrections"]} == {"C1", "C2"}
    with pytest.raises(case_store.CaseStoreError, match="Chief cannot"):
        store.work_context(case_id, "chief")


def test_save_requires_identity_and_is_one_use(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    case_id = create_case(store)
    record = store.load(case_id)
    with pytest.raises(SaveNotAuthorized):
        store.authorize_save(case_id)
    attest(store, case_id)
    store.authorize_save(case_id)
    store.require_save_authorization(case_id, record["identity_fingerprint"])
    store.consume_save_authorization(case_id, record["identity_fingerprint"])
    with pytest.raises(SaveNotAuthorized):
        store.require_save_authorization(case_id, record["identity_fingerprint"])
    with pytest.raises(SaveNotAuthorized):
        store.consume_save_authorization(case_id, record["identity_fingerprint"])


def test_identity_mismatch_blocks_save(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    case_id = create_case(store)
    result = store.mark_resolved(case_id, live_fingerprint="0" * 64, facts={"pending_target": True})
    assert result["status"] == "blocked"
    with pytest.raises(SaveNotAuthorized):
        store.authorize_save(case_id)


def test_only_known_corrections_can_be_approved(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    case_id = create_case(store, mode="correct")
    store.update_section(case_id, "corrections", [{"id": "C1", "approved": False}])
    store.approve_corrections(case_id, ["C1"])
    assert store.load(case_id)["corrections"][0]["approved"] is True
    with pytest.raises(case_store.CaseStoreError, match="Unknown correction"):
        store.approve_corrections(case_id, ["C2"])


def test_browser_claim_serializes_roles_and_close_cleans_claim(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = create_case(store)
    second = create_case(store)
    claims = BrowserClaimManager(store, ttl_seconds=60)
    claims.claim(first, "copier")
    with pytest.raises(BrowserBusy):
        claims.claim(second, "verifier")
    store.close(first)
    assert claims.status() == {"busy": False}
    claims.claim(second, "verifier")
    claims.release(second, "verifier")
    assert claims.status() == {"busy": False}


def test_expired_case_and_auxiliary_data_are_swept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(tmp_path)
    case_id = create_case(store)
    auxiliary = store.root / f".{case_id}.copier.log"
    auxiliary.write_text("sensitive fixture", encoding="utf-8")
    now = store.load(case_id)["expires_at"] + 1
    assert store.sweep_expired(now=now) == 1
    assert not auxiliary.exists()
    assert not (store.root / f"{case_id}.json").exists()


def test_workflow_routes_and_limits_writer_loops() -> None:
    assert initial_route("copy").assignee == "copier"
    assert initial_route("write").assignee == "writer"
    assert initial_route("verify").assignee == "verifier"
    assert after_verification("pass", 0).terminal is True
    assert after_verification("mixed", 0).assignee == "writer"
    assert after_verification("fail", 2).action == "block_for_user"
    assert after_write().assignee == "verifier"
    assert sanitize_summary("ok: 3 fields preserved") == "ok: 3 fields preserved"
    assert sanitize_summary("unsafe\nidentity@example.com") == "Result withheld; inspect the active secure case."


def test_role_policies_enforce_specialization() -> None:
    chief = ROLE_POLICIES["chief"]
    verifier = ROLE_POLICIES["verifier"]
    assert chief.profile == "devero" and not chief.can_read_devero
    assert verifier.can_read_devero and not verifier.can_write_pending and not verifier.can_save_pending
    assert ROLE_POLICIES["writer"].model == "gpt-5.6-sol"
    assert ROLE_POLICIES["verifier"].model == "gpt-5.6-sol"
    assert ROLE_POLICIES["copier"].model == "gpt-5.6-luna"
    assert all("sign" not in action for role in ROLE_POLICIES for action in allowed_actions(role))


def test_writer_skill_has_interventions_education_standard() -> None:
    workspace = Path(__file__).resolve().parent.parent
    skill = (workspace / "hermes" / "skills" / "devero-writer" / "SKILL.md").read_text(encoding="utf-8")
    assert "Interventions/Education Provided" in skill
    assert "intervention or assessment actually performed" in skill
    assert "education or reinforcement actually provided" in skill
    assert "observed patient/caregiver response" in skill
    assert "Do not write conversationally or add SOAP headings" in skill
    assert "Never manufacture understanding, teach-back" in skill
    assert "Do not reuse a generic paragraph across visits" in skill


def test_verifier_skill_checks_owner_interventions_education_standard() -> None:
    workspace = Path(__file__).resolve().parent.parent
    skill = (workspace / "hermes" / "skills" / "devero-verifier" / "SKILL.md").read_text(encoding="utf-8")
    assert "Owner documentation standard: interventions and education" in skill
    assert "Treat this as `USER_SUPPLIED`" in skill
    assert "Do not describe a style-only finding as CMS noncompliance" in skill
    assert "DVV-NOTE-INTERVENTION-EDUCATION-STYLE" in skill
    assert "DVV-NOTE-INTERVENTION-EDUCATION-SUPPORT" in skill
    assert "Route to `WRITER` only when every replacement fact is already fully supported" in skill


def test_copier_selection_and_shared_browser_patch() -> None:
    workspace = Path(__file__).resolve().parent.parent
    assert _script_for({"doc_type": "OASIS Resumption of Care"}).name == "devero_copier_oasis.py"
    assert _script_for({"doc_type": "Skilled Nurse Visit Note"}).name == "devero_copier_note.py"
    oasis = (workspace / "devero_copier_oasis.py").read_text(encoding="utf-8")
    assert 'os.getenv("DEVERO_KEEP_BROWSER_OPEN"' in oasis
    runner = (workspace / "devero_fleet" / "copier_runner.py").read_text(encoding="utf-8")
    assert "consume_save_authorization" in runner
    assert 'env["DEVERO_KEEP_BROWSER_OPEN"] = "true"' in runner
