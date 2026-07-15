"""Unit tests for the Drive query builder — the function that decides
whether search-by-patient-name actually works. Pure function, no mocking."""
import pytest

from app.services.drive import build_query
from app.services.google_api_utils import DriveQueryError


def test_patient_name_uses_name_contains():
    """patient_name is confirmed present in the filename (validated against
    a real file: JUN0049-SP-ELBA ZACARIAS.pdf) — must use `name contains`."""
    query = build_query({"patient_name": "Elba Zacarias"})
    assert "name contains 'Elba Zacarias'" in query
    assert "trashed = false" in query


def test_doctor_and_test_type_use_fulltext_not_name():
    """doctor/test_type are NOT part of the observed filename convention —
    routing them through `name contains` would silently zero out every
    search for a filename that doesn't carry that data. Must use fullText."""
    query = build_query({"doctor": "Dr. Gomez", "test_type": "hemograma"})
    assert "fullText contains 'Dr. Gomez'" in query
    assert "fullText contains 'hemograma'" in query
    assert "name contains 'Dr. Gomez'" not in query
    assert "name contains 'hemograma'" not in query


def test_accession_id_uses_name_contains():
    query = build_query({"accession_id": "JUN0049"})
    assert "name contains 'JUN0049'" in query


def test_combined_filters_and_together():
    query = build_query({"patient_name": "Elba Zacarias", "test_type": "hemograma"})
    assert "name contains 'Elba Zacarias'" in query
    assert "fullText contains 'hemograma'" in query
    assert " and " in query


def test_empty_filters_raises():
    with pytest.raises(ValueError):
        build_query({})


def test_quote_in_filter_is_escaped_not_injected():
    query = build_query({"patient_name": "O'Brien"})
    assert "\\'" in query
    # The raw unescaped quote must never appear standalone in the built query
    assert "name contains 'O'Brien'" not in query


def test_unsafe_characters_rejected_before_reaching_query_builder():
    with pytest.raises(DriveQueryError):
        build_query({"patient_name": "Elba\x00Zacarias"})
