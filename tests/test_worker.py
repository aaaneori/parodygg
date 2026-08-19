"""Scheduling: which days a run should collect."""

from datetime import date

import pytest

import worker

TODAY = date(2026, 8, 19)
YESTERDAY = date(2026, 8, 18)


def test_first_ever_run_collects_yesterday():
    """Today is still in progress, so the last complete day is yesterday."""
    assert worker.plan_days_to_collect(TODAY, last_success=None) == [YESTERDAY]


def test_nothing_to_do_when_already_current():
    assert worker.plan_days_to_collect(TODAY, last_success=YESTERDAY) == []


def test_one_missed_day_is_collected():
    assert worker.plan_days_to_collect(TODAY, date(2026, 8, 17)) == [YESTERDAY]


def test_a_gap_is_backfilled_day_by_day():
    """
    Each day is collected separately rather than as one wide window - daily
    rows are the unit the whole project is built on.
    """
    days = worker.plan_days_to_collect(TODAY, date(2026, 8, 15))

    assert days == [date(2026, 8, 16), date(2026, 8, 17), date(2026, 8, 18)]


def test_gap_beyond_the_limit_collects_yesterday_only():
    """
    Backfilling seven weeks would take days of API calls. The history keeps
    a hole, and the log says so.
    """
    assert worker.plan_days_to_collect(TODAY, date(2026, 7, 1)) == [YESTERDAY]


def test_backfill_stops_exactly_at_the_limit():
    """MAX_DAYS_TO_BACKFILL is inclusive."""
    limit = worker.MAX_DAYS_TO_BACKFILL
    last_success = date(2026, 8, 18 - limit)

    days = worker.plan_days_to_collect(TODAY, last_success)

    assert len(days) == limit
    assert days[-1] == YESTERDAY


def test_state_from_the_future_does_not_collect_backwards():
    """
    A clock change or a hand-edited last_run.json shouldn't make the worker
    walk into negative ranges.
    """
    assert worker.plan_days_to_collect(TODAY, date(2026, 9, 1)) == []
