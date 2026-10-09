from writer.fix_visit_note import _generate_grounded_ongoing_need, _generate_grounded_response


def test_legacy_fallback_does_not_restore_patient_specific_templates():
    package = {"task": {"patient": "Synthetic Patient", "target_date": "01/01/2026"}}
    interventions = "Parkinson disease monitoring and medication education."
    response = _generate_grounded_response(package, interventions)
    need = _generate_grounded_ongoing_need(package, interventions)
    assert "husband" not in response.lower()
    assert "reduced evening" not in response.lower()
    assert "post-mri" not in need.lower()
    assert "weekly" not in need.lower()
