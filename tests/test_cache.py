"""Raw match payload cache: round trip, corruption, retention."""

import sqlite3
from datetime import datetime, timedelta

from conftest import make_match


def test_payload_survives_the_round_trip(temp_cache):
    payload = make_match()
    temp_cache.cache_match("EUW1_1", payload)

    assert temp_cache.get_cached_match("EUW1_1") == payload


def test_unknown_match_is_a_miss(temp_cache):
    assert temp_cache.get_cached_match("EUW1_nope") is None


def test_payload_is_stored_compressed(temp_cache):
    """
    Match JSON repeats the same field names for all ten participants, so it
    compresses hard. Without this the cache would be tens of GB a month.
    """
    import json

    payload = make_match(participants=[{"championName": f"C{i}", "teamPosition": "MIDDLE",
                                        "win": True, "challenges": {}} for i in range(10)])
    raw_size = len(json.dumps(payload).encode())
    temp_cache.cache_match("EUW1_2", payload)

    conn = sqlite3.connect(temp_cache.CACHE_DB_FILE)
    stored = conn.execute('SELECT LENGTH(payload) FROM raw_matches').fetchone()[0]
    conn.close()

    assert stored < raw_size


def test_corrupted_entry_reads_as_a_miss(temp_cache):
    """A damaged row must make the caller refetch, not crash the run."""
    conn = sqlite3.connect(temp_cache.CACHE_DB_FILE)
    conn.execute("INSERT INTO raw_matches VALUES (?, ?, ?)",
                 ("EUW1_bad", datetime.now().strftime('%Y-%m-%d'), b"not zlib"))
    conn.commit()
    conn.close()

    assert temp_cache.get_cached_match("EUW1_bad") is None


def test_purge_removes_only_entries_past_retention(temp_cache):
    temp_cache.cache_match("fresh", make_match())

    old_date = (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d')
    conn = sqlite3.connect(temp_cache.CACHE_DB_FILE)
    conn.execute("UPDATE raw_matches SET fetched_at = ? WHERE match_id = 'stale'", (old_date,))
    conn.execute("INSERT INTO raw_matches VALUES ('stale', ?, ?)",
                 (old_date, sqlite3.Binary(b'x')))
    conn.commit()
    conn.close()

    temp_cache.purge_old_cache(30)

    conn = sqlite3.connect(temp_cache.CACHE_DB_FILE)
    remaining = {r[0] for r in conn.execute('SELECT match_id FROM raw_matches')}
    conn.close()

    assert remaining == {"fresh"}


def test_recaching_the_same_id_replaces_it(temp_cache):
    temp_cache.cache_match("EUW1_3", {"v": 1})
    temp_cache.cache_match("EUW1_3", {"v": 2})

    assert temp_cache.get_cached_match("EUW1_3") == {"v": 2}
    assert temp_cache.cache_stats()[0] == 1
