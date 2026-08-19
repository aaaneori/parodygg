"""
On-disk cache for raw Riot match payloads.

A match, once played, never changes - so the API response for it is safe to
keep forever. Two things this buys us:

  - a crashed run resumes instead of re-downloading everything
  - metrics can be recomputed for past days without touching the API at all

Lives in its own database because it's disposable. Deleting cache.db costs
nothing; deleting champions.db loses the actual project data.
"""

import json
import logging
import sqlite3
import zlib
from datetime import datetime, timedelta

from constants import CACHE_DB_FILE

log = logging.getLogger('worker')


def _get_connection():
    return sqlite3.connect(CACHE_DB_FILE)


def init_cache():
    conn = _get_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS raw_matches (
            match_id TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            payload BLOB NOT NULL
        )
    ''')
    # Purging filters on fetched_at, which isn't the primary key.
    conn.execute('CREATE INDEX IF NOT EXISTS idx_fetched_at ON raw_matches(fetched_at)')
    conn.commit()
    conn.close()


def get_cached_match(match_id):
    """Decoded payload, or None if we haven't seen this match."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT payload FROM raw_matches WHERE match_id = ?', (match_id,))
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None

    try:
        return json.loads(zlib.decompress(row[0]).decode('utf-8'))
    except (zlib.error, json.JSONDecodeError, UnicodeDecodeError) as e:
        # Corrupted entry - treat as a miss so the caller refetches.
        log.warning("Bad cache entry for %s, ignoring: %s", match_id, e)
        return None


def cache_match(match_id, payload):
    """
    Store a payload. Compressed because match JSON is hugely repetitive -
    the same couple hundred field names repeat for all ten participants,
    so zlib gets it down by roughly 7x.
    """
    blob = zlib.compress(json.dumps(payload, ensure_ascii=False).encode('utf-8'))

    conn = _get_connection()
    conn.execute(
        'INSERT OR REPLACE INTO raw_matches (match_id, fetched_at, payload) VALUES (?, ?, ?)',
        (match_id, datetime.now().strftime('%Y-%m-%d'), blob)
    )
    conn.commit()
    conn.close()


def purge_old_cache(retention_days):
    """
    Trim the tail. Runs every time the worker starts, so the cache holds a
    rolling window rather than growing without bound.

    Note SQLite won't shrink the file on DELETE - freed pages get reused
    instead. Size plateaus, which is exactly what we want here.
    """
    cutoff = (datetime.now() - timedelta(days=retention_days)).strftime('%Y-%m-%d')

    conn = _get_connection()
    cursor = conn.execute('DELETE FROM raw_matches WHERE fetched_at < ?', (cutoff,))
    removed = cursor.rowcount
    conn.commit()
    conn.close()

    if removed > 0:
        log.info("Cache: dropped %s entries older than %s", removed, cutoff)


def cache_stats():
    """Entry count and approximate size, for logging."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)), 0) FROM raw_matches')
    count, total_bytes = cursor.fetchone()
    conn.close()
    return count, total_bytes