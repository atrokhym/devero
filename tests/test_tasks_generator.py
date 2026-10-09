import json
import os
import sys
import tempfile
from unittest.mock import Mock, patch
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from devero_tasks import parse_date_safe, select_newest_completed_note, scrape_all_pending_pages
from devero_copier_note import load_tasks_config

def test_parse_date_safe():
    assert parse_date_safe("08/03/2026").strftime("%m/%d/%Y") == "08/03/2026"
    assert parse_date_safe("07/22/2026 06:16:40 PM EDT").strftime("%m/%d/%Y") == "07/22/2026"
    assert parse_date_safe("") is None
    assert parse_date_safe(None) is None
    print("[PASS] test_parse_date_safe")


def test_patient_filters_across_pages():
    rows = [
        {"patient": name, "form": "Visit", "target_date": "10/06/2026", "caregiver": "Nurse"}
        for name in ("Smith, Alice", "Jones, Bob", "Brown, Carol")
    ]
    for filters, expected in ((None, rows), ([" SMITH ", "bOb"], rows[:2]), (["missing"], [])):
        with patch("devero_tasks.set_pending_table_page_size_max"), patch(
            "devero_tasks.scrape_pending_table_current_page",
            side_effect=[
                {"rows": rows[:1], "pagination": {"hasNext": True}},
                {"rows": rows[1:], "pagination": {"hasNext": False}},
            ],
        ) as scrape:
            assert scrape_all_pending_pages(Mock(), "Visit", "Nurse", filters) == expected
            assert scrape.call_count == 2
            assert scrape.call_args.args[1:] == ("Visit", "Nurse")


def test_select_newest_completed_note_across_unordered_episodes():
    rows = [
        {
            "form": "Skilled Nurse Visit Note",
            "date": "07/10/2026",
            "status": "Completed",
            "rowText": "Skilled Nurse Visit Note 07/10/2026 Completed",
            "user": "Nurse A",
            "episode": "Episode shown first",
        },
        {
            "form": "Skilled Nurse Visit Note",
            "date": "07/24/2026",
            "status": "Sent to Office",
            "rowText": "Skilled Nurse Visit Note 07/24/2026 Sent to Office",
            "user": "Nurse B",
            "episode": "Episode shown later",
        },
        {
            "form": "Skilled Nurse Visit Note",
            "date": "07/29/2026",
            "status": "Pending",
            "rowText": "Skilled Nurse Visit Note 07/29/2026 Pending",
            "user": "Nurse C",
            "episode": "Episode shown last",
        },
    ]

    newest = select_newest_completed_note(
        rows,
        "Skilled Nurse Visit Note",
        before_date=parse_date_safe("08/01/2026"),
    )

    assert newest["date_str"] == "07/24/2026"
    assert newest["episode"] == "Episode shown later"


def test_select_newest_completed_note_never_copies_backward():
    rows = [
        {
            "form": "Skilled Nurse Visit Note",
            "date": "08/05/2026",
            "status": "Completed",
            "rowText": "Skilled Nurse Visit Note 08/05/2026 Completed",
        },
        {
            "form": "Skilled Nurse Visit Note",
            "date": "07/31/2026",
            "status": "Completed",
            "rowText": "Skilled Nurse Visit Note 07/31/2026 Completed",
        },
    ]

    newest = select_newest_completed_note(
        rows,
        "Skilled Nurse Visit Note",
        before_date=parse_date_safe("08/03/2026"),
    )

    assert newest["date_str"] == "07/31/2026"


def test_task_json_compatibility():
    sample_tasks = [
        {
            "patient": "FixtureOne, Patient",
            "caregiver": "Example, Clinician (RN)",
            "doc_type": "Skilled Nurse Visit Note",
            "source_date": "07/22/2026",
            "target_date": "08/03/2026"
        },
        {
            "patient": "FixtureOne, Patient",
            "caregiver": "Example, Clinician (RN)",
            "doc_type": "Skilled Nurse Visit Note",
            "source_date": "07/22/2026",
            "target_date": "08/06/2026"
        },
        {
            "patient": "FixtureTwo, Patient",
            "caregiver": "Example, Clinician (RN)",
            "doc_type": "Skilled Nurse Visit Note",
            "source_date": "07/17/2026",
            "target_date": "08/03/2026"
        }
    ]
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_json_file = os.path.join(tmp_dir, "tasks_note.json")
        with open(test_json_file, "w", encoding="utf-8") as f:
            json.dump(sample_tasks, f, indent=2)
            
        loaded = load_tasks_config(test_json_file)
        assert len(loaded) == 3
        assert loaded[0]["patient"] == "FixtureOne, Patient"
        assert loaded[0]["source_date"] == "07/22/2026"
        assert loaded[0]["target_date"] == "08/03/2026"
        print("[PASS] test_task_json_compatibility")

if __name__ == "__main__":
    test_patient_filters_across_pages()
    test_parse_date_safe()
    test_select_newest_completed_note_across_unordered_episodes()
    test_select_newest_completed_note_never_copies_backward()
    test_task_json_compatibility()
    print("\nAll unit tests passed successfully!")
