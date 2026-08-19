"""
last_run.json.

Both fields live in one file, and the previous implementation wrote each of
them separately - which is exactly how one silently erases the other.
"""

from datetime import date

import pytest

import state


@pytest.fixture(autouse=True)
def temp_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'LAST_RUN_FILE', str(tmp_path / 'last_run.json'))


def test_missing_file_reads_as_never_run():
    assert state.get_last_success_date() is None
    assert state.get_last_known_patch() is None


def test_marking_a_day_preserves_the_known_patch():
    state.mark_patch_known("26.16")
    state.mark_day_complete(date(2026, 8, 18))

    assert state.get_last_known_patch() == "26.16"
    assert state.get_last_success_date() == date(2026, 8, 18)


def test_marking_a_patch_preserves_the_last_day():
    state.mark_day_complete(date(2026, 8, 18))
    state.mark_patch_known("26.17")

    assert state.get_last_success_date() == date(2026, 8, 18)
    assert state.get_last_known_patch() == "26.17"


def test_patch_recorded_before_any_day_does_not_break_reading():
    """
    The old code raised KeyError here - and this is precisely the state the
    very first run produced.
    """
    state.mark_patch_known("26.16")

    assert state.get_last_success_date() is None


def test_values_are_overwritten_not_appended():
    state.mark_day_complete(date(2026, 8, 17))
    state.mark_day_complete(date(2026, 8, 18))

    assert state.get_last_success_date() == date(2026, 8, 18)
