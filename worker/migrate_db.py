"""
One-off migration: single denormalized champion_daily_stats -> three tables.

    daily_runs           (date, patch) -> matches_processed
    champion_daily_bans  (date, patch, champion) -> bans
    champion_daily_stats (date, patch, champion, role) -> games, wins, extended

Run it by hand, once:

    python migrate_db.py

Safety:
  - copies champions.db to champions.db.backup-<date> before touching anything
  - validates totals (games, wins, bans, matches, every extended column)
    before COMMIT; any mismatch rolls back and the old table stays intact
  - refuses to run twice
"""

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from constants import DB_FILE
from database import EXTENDED_STAT_COLUMNS, SCHEMA_STATEMENTS


def fail(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


def table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def main():
    db_path = Path(DB_FILE)
    if not db_path.exists():
        fail(f"{DB_FILE} not found - nothing to migrate.")

    # --- backup first, before any connection is even opened for writing
    backup_path = db_path.with_name(
        f"{db_path.name}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy2(db_path, backup_path)
    print(f"Backup written: {backup_path}")

    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys=ON")

    # --- preconditions
    if not table_exists(conn, 'champion_daily_stats'):
        fail("champion_daily_stats not found.")
    if table_exists(conn, 'daily_runs'):
        fail("daily_runs already exists - migration appears to have run already.")

    old_cols = {row[1] for row in conn.execute("PRAGMA table_info(champion_daily_stats)")}
    if 'patch' not in old_cols:
        fail("old table has no 'patch' column - unexpected schema.")
    missing = [c for c in EXTENDED_STAT_COLUMNS if c not in old_cols]
    if missing:
        fail(f"old table is missing extended columns {missing} - "
             f"run the worker once on the current code first.")

    # Sanity: bans must be identical across a champion's roles within a day.
    # That's the invariant that makes DISTINCT collapsing valid.
    bad = conn.execute('''
        SELECT date, champion, COUNT(DISTINCT bans)
        FROM champion_daily_stats
        GROUP BY date, champion
        HAVING COUNT(DISTINCT bans) > 1
    ''').fetchall()
    if bad:
        fail(f"inconsistent bans across roles for {len(bad)} (date, champion) "
             f"pairs, e.g. {bad[:3]} - refusing to guess.")

    ext_cols = ', '.join(EXTENDED_STAT_COLUMNS)
    ext_sums = ', '.join(f'SUM({c})' for c in EXTENDED_STAT_COLUMNS)

    # --- expected totals, taken with the same dedup logic the old queries used
    expected = {}
    expected['runs'] = conn.execute('''
        SELECT COUNT(*), COALESCE(SUM(matches_processed), 0) FROM (
            SELECT DISTINCT date, patch, matches_processed FROM champion_daily_stats
        )
    ''').fetchone()
    expected['bans'] = conn.execute('''
        SELECT COALESCE(SUM(bans), 0) FROM (
            SELECT DISTINCT date, champion, bans FROM champion_daily_stats
        )
    ''').fetchone()[0]
    expected['stats'] = conn.execute(f'''
        SELECT COUNT(*), COALESCE(SUM(games), 0), COALESCE(SUM(wins), 0), {ext_sums}
        FROM champion_daily_stats WHERE role != 'UNPICKED'
    ''').fetchone()

    print(f"Old table: {expected['stats'][0]} stat rows (excl. UNPICKED), "
          f"{expected['runs'][0]} day-patch runs, "
          f"{expected['bans']} total bans.")

    try:
        cur = conn.cursor()

        # New tables under final names - old one is renamed away first, so
        # SCHEMA_STATEMENTS from database.py can be reused verbatim.
        cur.execute("ALTER TABLE champion_daily_stats RENAME TO old_stats")
        for stmt in SCHEMA_STATEMENTS:
            cur.execute(stmt)

        cur.execute('''
            INSERT INTO daily_runs (date, patch, matches_processed)
            SELECT DISTINCT date, patch, matches_processed FROM old_stats
        ''')

        # bans > 0 only: "no bans" is the absence of a row in the new world
        cur.execute('''
            INSERT INTO champion_daily_bans (date, patch, champion, bans)
            SELECT date, patch, champion, bans FROM (
                SELECT DISTINCT date, patch, champion, bans FROM old_stats
            ) WHERE bans > 0
        ''')

        cur.execute(f'''
            INSERT INTO champion_daily_stats
                (date, patch, champion, role, games, wins, {ext_cols})
            SELECT date, patch, champion, role, games, wins, {ext_cols}
            FROM old_stats WHERE role != 'UNPICKED'
        ''')

        # --- validate before committing anything
        got_runs = cur.execute(
            'SELECT COUNT(*), COALESCE(SUM(matches_processed), 0) FROM daily_runs'
        ).fetchone()
        got_bans = cur.execute(
            'SELECT COALESCE(SUM(bans), 0) FROM champion_daily_bans'
        ).fetchone()[0]
        got_stats = cur.execute(
            f'SELECT COUNT(*), COALESCE(SUM(games), 0), COALESCE(SUM(wins), 0), {ext_sums} '
            f'FROM champion_daily_stats'
        ).fetchone()

        if got_runs != expected['runs']:
            raise ValueError(f"daily_runs mismatch: {got_runs} != {expected['runs']}")
        if got_bans != expected['bans']:
            raise ValueError(f"bans mismatch: {got_bans} != {expected['bans']}")
        if got_stats != expected['stats']:
            raise ValueError(f"stats mismatch: {got_stats} != {expected['stats']}")

        cur.execute("DROP TABLE old_stats")
        conn.commit()

    except Exception as e:
        conn.rollback()
        fail(f"migration rolled back, database unchanged: {e}")

    conn.execute("VACUUM")
    conn.close()

    print("Migration complete.")
    print(f"  daily_runs:           {got_runs[0]} rows")
    print(f"  champion_daily_bans:  ban total {got_bans}")
    print(f"  champion_daily_stats: {got_stats[0]} rows")
    print(f"Backup kept at {backup_path} - delete it once the site looks right.")


if __name__ == "__main__":
    main()