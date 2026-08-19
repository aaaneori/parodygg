import logging
import sqlite3

from constants import DB_FILE

log = logging.getLogger('worker')

# Extended stats for the Champion Stats sidebar. Every column is a sum of
# raw per-match values for that day; we divide by games on read.
#
# For the "/min" ones we store the already-computed per-match ratio
# (dmg / minutes), not raw dmg and duration separately - so the average is
# mean(per-match ratios), not sum(dmg) / sum(minutes). Different numbers.
#
# Adding a metric later: add it here and to the DDL below, collect it in
# worker's process_matches(), and render it on the frontend. For an existing
# database, add the column with a one-line ALTER TABLE.
EXTENDED_STAT_COLUMNS = [
    'kills', 'deaths', 'assists', 'solo_kills',
    'kill_participation_pct', 'first_bloods',
    'physical_dmg', 'magic_dmg', 'dmg_taken', 'team_dmg_pct',
    'dmg_per_min', 'cs_per_min', 'gold_per_min', 'vision_per_min',
    'control_wards'
]

_EXT_DDL = ',\n            '.join(
    f"{c} {'REAL' if c.endswith('_pct') or c.endswith('_per_min') else 'INTEGER'} NOT NULL DEFAULT 0"
    for c in EXTENDED_STAT_COLUMNS
)

# Three tables, one per real entity:
#   daily_runs           - one row per collection run (a day within a patch)
#   champion_daily_bans  - bans are per champion, roles don't exist for bans;
#                          bans=0 is simply the absence of a row
#   champion_daily_stats - per (champion, role); a champion banned but never
#                          picked has no row here (the old UNPICKED pseudo-role
#                          is gone)
#
# The key is (date, patch), not just date: on patch-release day a single date
# can legitimately hold two runs, one per patch. ON DELETE CASCADE lets a
# backfill wipe and rewrite a day atomically by deleting the parent row.
#
# Shared with migrate_db.py so the DDL can't drift between the two.
SCHEMA_STATEMENTS = [
    '''
    CREATE TABLE IF NOT EXISTS daily_runs (
        date TEXT NOT NULL,
        patch TEXT NOT NULL,
        matches_processed INTEGER NOT NULL,
        PRIMARY KEY (date, patch)
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS champion_daily_bans (
        date TEXT NOT NULL,
        patch TEXT NOT NULL,
        champion TEXT NOT NULL,
        bans INTEGER NOT NULL,
        PRIMARY KEY (date, patch, champion),
        FOREIGN KEY (date, patch) REFERENCES daily_runs (date, patch) ON DELETE CASCADE
    )
    ''',
    f'''
    CREATE TABLE IF NOT EXISTS champion_daily_stats (
        date TEXT NOT NULL,
        patch TEXT NOT NULL,
        champion TEXT NOT NULL,
        role TEXT NOT NULL,
        games INTEGER NOT NULL,
        wins INTEGER NOT NULL,
        {_EXT_DDL},
        PRIMARY KEY (date, patch, champion, role),
        FOREIGN KEY (date, patch) REFERENCES daily_runs (date, patch) ON DELETE CASCADE
    )
    ''',
    'CREATE INDEX IF NOT EXISTS idx_stats_patch ON champion_daily_stats (patch)',
    'CREATE INDEX IF NOT EXISTS idx_stats_champion ON champion_daily_stats (champion)',
    'CREATE INDEX IF NOT EXISTS idx_bans_patch ON champion_daily_bans (patch)',
]


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    # Off by default in SQLite; without it ON DELETE CASCADE does nothing.
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _old_schema_present(conn):
    """True if the pre-normalization single-table layout is detected."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if 'daily_runs' in tables:
        return False
    if 'champion_daily_stats' not in tables:
        return False
    cols = {row[1] for row in conn.execute("PRAGMA table_info(champion_daily_stats)")}
    return 'patch' in cols and 'matches_processed' in cols


def create_table_if_not_exists():
    conn = get_connection()

    # A schema change this size should happen once, by hand, with a backup -
    # not silently inside a scheduled run.
    if _old_schema_present(conn):
        conn.close()
        raise RuntimeError(
            "Old single-table schema detected. Run 'python migrate_db.py' once "
            "to migrate (it makes a backup first), then start the worker again."
        )

    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    conn.commit()
    conn.close()


def insert_daily_stats(stat_rows, ban_rows, date, patch, matches_processed):
    """
    Write one collected day in a single transaction.

    Deleting the daily_runs row first cascades into bans and stats, so a
    backfill re-run replaces the whole day atomically instead of upserting
    row by row - a crash mid-write can no longer leave a half-written day.

    stat_rows: [{champion, role, games, wins, <extended...>}], picked only
    ban_rows:  [{champion, bans}], bans > 0 only
    """
    ext_cols = ', '.join(EXTENDED_STAT_COLUMNS)
    ext_marks = ', '.join(['?'] * len(EXTENDED_STAT_COLUMNS))

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('DELETE FROM daily_runs WHERE date = ? AND patch = ?', (date, patch))
        cur.execute(
            'INSERT INTO daily_runs (date, patch, matches_processed) VALUES (?, ?, ?)',
            (date, patch, matches_processed)
        )
        cur.executemany(
            'INSERT INTO champion_daily_bans (date, patch, champion, bans) VALUES (?, ?, ?, ?)',
            [(date, patch, r['champion'], r['bans']) for r in ban_rows if r['bans'] > 0]
        )
        cur.executemany(
            f'''INSERT INTO champion_daily_stats
                (date, patch, champion, role, games, wins, {ext_cols})
                VALUES (?, ?, ?, ?, ?, ?, {ext_marks})''',
            [
                (date, patch, r['champion'], r['role'], r['games'], r['wins'],
                 *[r.get(c, 0) for c in EXTENDED_STAT_COLUMNS])
                for r in stat_rows
            ]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_last_updated_date(patch):
    """Most recent date with data for this patch - drives the "updated" label."""
    conn = get_connection()
    result = conn.execute(
        'SELECT MAX(date) FROM daily_runs WHERE patch = ?', (patch,)
    ).fetchone()[0]
    conn.close()
    return result


def get_patch_summary(patch):
    """
    Cumulative stats for a patch, from its first day to today.

    One row per (champion, role). Bans are per champion with no role split -
    you ban the champion, not the role - so banrate repeats across a
    champion's rows. Champions banned but never picked get a single row with
    role UNPICKED and zero games: that pseudo-role no longer exists in the
    database, but the export format keeps it so the frontend contract is
    unchanged.

    Percentages come from summed raw counts, never averaged across days.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT champion, role, SUM(games), SUM(wins)
        FROM champion_daily_stats
        WHERE patch = ?
        GROUP BY champion, role
    ''', (patch,))
    champion_role_rows = cursor.fetchall()

    cursor.execute('''
        SELECT champion, SUM(bans)
        FROM champion_daily_bans
        WHERE patch = ?
        GROUP BY champion
    ''', (patch,))
    champion_bans = dict(cursor.fetchall())

    total_matches = cursor.execute(
        'SELECT COALESCE(SUM(matches_processed), 0) FROM daily_runs WHERE patch = ?',
        (patch,)
    ).fetchone()[0]

    conn.close()

    rows = []
    picked = set()
    for champion, role, games, wins in champion_role_rows:
        picked.add(champion)
        bans = champion_bans.get(champion, 0)

        winrate = (wins / games * 100) if games > 0 else 0
        pickrate = (games / total_matches * 100) if total_matches > 0 else 0
        banrate = (bans / (total_matches * 2) * 100) if total_matches > 0 else 0

        rows.append({
            "champion": champion,
            "role": role,
            "games": games,
            "wins": wins,
            "winrate": round(winrate, 2),
            "pickrate": round(pickrate, 2),
            "bans": bans,
            "banrate": round(banrate, 2)
        })

    # banned-but-never-picked: synthesized for the export, absent in storage
    for champion, bans in champion_bans.items():
        if champion in picked:
            continue
        banrate = (bans / (total_matches * 2) * 100) if total_matches > 0 else 0
        rows.append({
            "champion": champion, "role": "UNPICKED",
            "games": 0, "wins": 0, "winrate": 0, "pickrate": 0,
            "bans": bans, "banrate": round(banrate, 2)
        })

    return rows, total_matches


def get_all_champions_history():
    """
    Raw daily history for every champion across every patch, in one query
    per table. Returns {champion: [{date, patch, role, games, wins, bans,
    matches_processed}, ...]}, each list sorted by date.

    Deliberately raw counts, no precomputed winrate/pickrate: the frontend
    groups by arbitrary periods, and percentages for a period have to come
    from that period's summed counts, not from averaging daily percentages.

    The JSON shape matches the old export exactly: each stat row carries its
    day's ban count for the champion, and ban-only days appear as UNPICKED
    rows with zero games. Storage is normalized; the contract is not.
    """
    conn = get_connection()
    cursor = conn.cursor()

    runs = {
        (date, patch): mp
        for date, patch, mp in cursor.execute(
            'SELECT date, patch, matches_processed FROM daily_runs'
        )
    }
    bans = {
        (date, patch, champion): b
        for date, patch, champion, b in cursor.execute(
            'SELECT date, patch, champion, bans FROM champion_daily_bans'
        )
    }

    history_by_champion = {}

    covered = set()  # (date, patch, champion) pairs that have stat rows
    for champion, date, patch, role, games, wins in cursor.execute('''
        SELECT champion, date, patch, role, games, wins
        FROM champion_daily_stats
        ORDER BY champion, date
    '''):
        covered.add((date, patch, champion))
        history_by_champion.setdefault(champion, []).append({
            "date": date,
            "patch": patch,
            "role": role,
            "games": games,
            "wins": wins,
            "bans": bans.get((date, patch, champion), 0),
            "matches_processed": runs[(date, patch)]
        })

    conn.close()

    # ban-only days: banned that day, picked in no role
    for (date, patch, champion), ban_count in bans.items():
        if (date, patch, champion) in covered:
            continue
        history_by_champion.setdefault(champion, []).append({
            "date": date,
            "patch": patch,
            "role": "UNPICKED",
            "games": 0,
            "wins": 0,
            "bans": ban_count,
            "matches_processed": runs[(date, patch)]
        })

    for entries in history_by_champion.values():
        entries.sort(key=lambda e: e["date"])

    return history_by_champion


def get_champion_extended_stats(champion, patch, role=None):
    """
    Extended stats for one champion over a patch, summed across days and
    divided by games on read.

    role=None sums every role (the "All" tab). A specific role filters to it.
    Returns None when there are no games, so the caller can skip rendering.
    """
    conn = get_connection()
    cursor = conn.cursor()

    sum_columns_sql = ', '.join(f'SUM({col})' for col in EXTENDED_STAT_COLUMNS)

    if role is None:
        cursor.execute(f'''
            SELECT SUM(games), {sum_columns_sql}
            FROM champion_daily_stats
            WHERE champion = ? AND patch = ?
        ''', (champion, patch))
    else:
        cursor.execute(f'''
            SELECT SUM(games), {sum_columns_sql}
            FROM champion_daily_stats
            WHERE champion = ? AND patch = ? AND role = ?
        ''', (champion, patch, role))

    result = cursor.fetchone()
    conn.close()

    total_games = result[0] or 0
    if total_games == 0:
        return None

    extended_sums = dict(zip(EXTENDED_STAT_COLUMNS, [v or 0 for v in result[1:]]))

    stats = {'games': total_games}
    for col, total in extended_sums.items():
        if col == 'first_bloods':
            # A count of games, not a summed percentage - so it becomes a
            # share (*100) rather than a plain average like the rest.
            stats['first_blood_pct'] = round((total / total_games) * 100, 2)
        else:
            stats[col] = round(total / total_games, 2)

    return stats


def get_tracked_patches():
    """Every patch we have any data for."""
    conn = get_connection()
    result = [row[0] for row in conn.execute(
        'SELECT DISTINCT patch FROM daily_runs ORDER BY patch'
    )]
    conn.close()
    return result