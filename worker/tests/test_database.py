"""
Storage and aggregation.

The invariants here were decided deliberately during the project; these
tests turn "we agreed to do it this way" into something checkable.
"""

import sqlite3

import pytest

import database


def stat_row(champion, role, games, wins, **extended):
    row = {"champion": champion, "role": role, "games": games, "wins": wins}
    row.update(extended)
    return row


# --- percentages ------------------------------------------------------

def test_banrate_divides_by_two_teams_per_match(temp_db):
    """Every match has two ban phases, so the denominator is matches * 2."""
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=10, wins=5)],
        [{"champion": "Ahri", "bans": 50}],
        "2026-08-16", "26.16", matches_processed=100,
    )

    rows, total = database.get_patch_summary("26.16")
    ahri = next(r for r in rows if r["champion"] == "Ahri")

    assert total == 100
    assert ahri["banrate"] == 25.0  # 50 / (100 * 2)


def test_percentages_come_from_sums_not_from_daily_averages(temp_db):
    """
    Day one: 10 games, 8 wins (80%). Day two: 90 games, 45 wins (50%).
    Averaging the daily rates gives 65%; the correct answer is 53% -
    53 wins out of 100 games. Bigger days must carry more weight.
    """
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=10, wins=8)], [],
        "2026-08-16", "26.16", matches_processed=100,
    )
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=90, wins=45)], [],
        "2026-08-17", "26.16", matches_processed=100,
    )

    rows, _ = database.get_patch_summary("26.16")
    ahri = next(r for r in rows if r["champion"] == "Ahri")

    assert ahri["games"] == 100
    assert ahri["winrate"] == 53.0
    assert ahri["winrate"] != 65.0, "daily percentages must not be averaged"


def test_matches_processed_is_summed_per_day_not_per_row(temp_db):
    """
    Three champion rows in one day still mean one day's worth of matches.
    Summing per row would triple the denominator and crush every pickrate.
    """
    database.insert_daily_stats(
        [
            stat_row("Ahri", "MIDDLE", games=10, wins=5),
            stat_row("Zed", "MIDDLE", games=10, wins=5),
            stat_row("Yasuo", "TOP", games=10, wins=5),
        ],
        [], "2026-08-16", "26.16", matches_processed=500,
    )

    _, total = database.get_patch_summary("26.16")

    assert total == 500


def test_pickrate_is_games_over_matches(temp_db):
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=250, wins=125)], [],
        "2026-08-16", "26.16", matches_processed=1000,
    )

    rows, _ = database.get_patch_summary("26.16")

    assert next(r for r in rows if r["champion"] == "Ahri")["pickrate"] == 25.0


def test_zero_matches_does_not_divide_by_zero(temp_db):
    """An empty patch must return zeros rather than raise."""
    rows, total = database.get_patch_summary("99.99")

    assert rows == []
    assert total == 0


# --- bans without picks -----------------------------------------------

def test_banned_but_unpicked_champion_appears_as_unpicked_row(temp_db):
    """
    Storage has no UNPICKED role any more - a champion nobody picked simply
    has no stat row. The export still synthesizes one so the frontend
    contract stays unchanged.
    """
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=10, wins=5)],
        [{"champion": "Annie", "bans": 40}],
        "2026-08-16", "26.16", matches_processed=100,
    )

    rows, _ = database.get_patch_summary("26.16")
    annie = next(r for r in rows if r["champion"] == "Annie")

    assert annie["role"] == "UNPICKED"
    assert annie["games"] == 0
    assert annie["banrate"] == 20.0


def test_picked_champion_never_gets_an_unpicked_row(temp_db):
    """
    Before normalization a champion picked on some days and only banned on
    others ended up with both a real row and a phantom UNPICKED one.
    """
    database.insert_daily_stats(
        [], [{"champion": "Rammus", "bans": 4}],
        "2026-08-16", "26.16", matches_processed=100,
    )
    database.insert_daily_stats(
        [stat_row("Rammus", "JUNGLE", games=5, wins=3)],
        [{"champion": "Rammus", "bans": 4}],
        "2026-08-17", "26.16", matches_processed=100,
    )

    rows, _ = database.get_patch_summary("26.16")
    rammus = [r for r in rows if r["champion"] == "Rammus"]

    assert len(rammus) == 1
    assert rammus[0]["role"] == "JUNGLE"
    assert rammus[0]["bans"] == 8


def test_zero_ban_rows_are_not_stored(temp_db):
    """No bans means no row, not a row holding a zero."""
    database.insert_daily_stats(
        [], [{"champion": "Ahri", "bans": 0}, {"champion": "Zed", "bans": 3}],
        "2026-08-16", "26.16", matches_processed=100,
    )

    conn = sqlite3.connect(temp_db)
    stored = [r[0] for r in conn.execute('SELECT champion FROM champion_daily_bans')]
    conn.close()

    assert stored == ["Zed"]


# --- writing a day ----------------------------------------------------

def test_recollecting_a_day_replaces_it_entirely(temp_db):
    """
    A backfill re-run must not merge with the previous attempt. Deleting the
    parent daily_runs row cascades into both child tables.
    """
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=10, wins=5)],
        [{"champion": "Zed", "bans": 5}],
        "2026-08-16", "26.16", matches_processed=100,
    )
    database.insert_daily_stats(
        [stat_row("Ahri", "TOP", games=3, wins=1)],
        [{"champion": "Yasuo", "bans": 9}],
        "2026-08-16", "26.16", matches_processed=105,
    )

    conn = sqlite3.connect(temp_db)
    stats = conn.execute(
        'SELECT champion, role, games FROM champion_daily_stats').fetchall()
    bans = conn.execute('SELECT champion, bans FROM champion_daily_bans').fetchall()
    runs = conn.execute('SELECT matches_processed FROM daily_runs').fetchall()
    conn.close()

    assert stats == [("Ahri", "TOP", 3)]
    assert bans == [("Yasuo", 9)]
    assert runs == [(105,)]


def test_same_date_under_two_patches_coexist(temp_db):
    """
    The primary key is (date, patch): on patch-release day one date can
    legitimately hold two runs.
    """
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=10, wins=5)], [],
        "2026-08-16", "26.16", matches_processed=100,
    )
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=20, wins=10)], [],
        "2026-08-16", "26.17", matches_processed=200,
    )

    assert database.get_tracked_patches() == ["26.16", "26.17"]
    assert database.get_patch_summary("26.16")[1] == 100
    assert database.get_patch_summary("26.17")[1] == 200


def test_old_schema_is_refused_rather_than_migrated_silently(tmp_path, monkeypatch):
    """
    A migration this size belongs in a script with a backup, not in a
    scheduled run that nobody is watching.
    """
    db_file = str(tmp_path / 'old.db')
    conn = sqlite3.connect(db_file)
    conn.execute('''CREATE TABLE champion_daily_stats (
        date TEXT, patch TEXT, champion TEXT, role TEXT,
        games INTEGER, wins INTEGER, bans INTEGER, matches_processed INTEGER)''')
    conn.commit()
    conn.close()

    monkeypatch.setattr(database, 'DB_FILE', db_file)

    with pytest.raises(RuntimeError, match="migrate_db.py"):
        database.create_table_if_not_exists()


# --- extended stats ---------------------------------------------------

def test_extended_stats_average_over_games(temp_db):
    """Stored as sums, divided by games on read."""
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=10, wins=5, kills=50, dmg_per_min=9000)],
        [], "2026-08-16", "26.16", matches_processed=100,
    )

    stats = database.get_champion_extended_stats("Ahri", "26.16")

    assert stats["games"] == 10
    assert stats["kills"] == 5.0
    assert stats["dmg_per_min"] == 900.0


def test_extended_stats_all_roles_sums_every_role(temp_db):
    database.insert_daily_stats(
        [
            stat_row("Ahri", "MIDDLE", games=10, wins=5, kills=50),
            stat_row("Ahri", "TOP", games=10, wins=5, kills=30),
        ],
        [], "2026-08-16", "26.16", matches_processed=100,
    )

    all_roles = database.get_champion_extended_stats("Ahri", "26.16")
    mid_only = database.get_champion_extended_stats("Ahri", "26.16", role="MIDDLE")

    assert all_roles["games"] == 20
    assert all_roles["kills"] == 4.0   # (50 + 30) / 20
    assert mid_only["games"] == 10
    assert mid_only["kills"] == 5.0


def test_first_blood_becomes_a_percentage(temp_db):
    """first_bloods counts games, so it scales to a share rather than a mean."""
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=10, wins=5, first_bloods=2)],
        [], "2026-08-16", "26.16", matches_processed=100,
    )

    stats = database.get_champion_extended_stats("Ahri", "26.16")

    assert stats["first_blood_pct"] == 20.0
    assert "first_bloods" not in stats


def test_extended_stats_missing_champion_returns_none(temp_db):
    """The sidebar hides itself instead of showing a column of zeroes."""
    assert database.get_champion_extended_stats("Nobody", "26.16") is None


# --- history export ---------------------------------------------------

def test_history_carries_each_days_ban_count(temp_db):
    database.insert_daily_stats(
        [stat_row("Ahri", "MIDDLE", games=10, wins=5)],
        [{"champion": "Ahri", "bans": 30}],
        "2026-08-16", "26.16", matches_processed=100,
    )

    entry = database.get_all_champions_history()["Ahri"][0]

    assert entry["bans"] == 30
    assert entry["matches_processed"] == 100
    assert entry["patch"] == "26.16"


def test_history_includes_ban_only_days(temp_db):
    """
    A day where a champion was banned but never picked still belongs on the
    ban-rate chart, so it appears as an UNPICKED entry with zero games.
    """
    database.insert_daily_stats(
        [], [{"champion": "Annie", "bans": 12}],
        "2026-08-16", "26.16", matches_processed=100,
    )

    entries = database.get_all_champions_history()["Annie"]

    assert len(entries) == 1
    assert entries[0]["role"] == "UNPICKED"
    assert entries[0]["games"] == 0
    assert entries[0]["bans"] == 12


def test_history_is_sorted_by_date(temp_db):
    for date in ("2026-08-18", "2026-08-16", "2026-08-17"):
        database.insert_daily_stats(
            [stat_row("Ahri", "MIDDLE", games=1, wins=1)], [],
            date, "26.16", matches_processed=100,
        )

    dates = [e["date"] for e in database.get_all_champions_history()["Ahri"]]

    assert dates == sorted(dates)


def test_last_updated_is_the_newest_day_of_the_patch(temp_db):
    for date in ("2026-08-16", "2026-08-18", "2026-08-17"):
        database.insert_daily_stats(
            [stat_row("Ahri", "MIDDLE", games=1, wins=1)], [],
            date, "26.16", matches_processed=100,
        )

    assert database.get_last_updated_date("26.16") == "2026-08-18"
