"""
Entry point. Decides which days need collecting, runs them, exports.

Everything else lives in its own module:
    riot_api    - HTTP, retries, rate limits
    cache       - raw match payloads on disk
    ddragon     - champion id/name map
    collector   - match details -> aggregated daily rows
    database    - SQL
    exporter    - database -> JSON files -> git push
    state       - last_run.json
"""

from datetime import datetime, timedelta

from logging_setup import setup_logging

log = setup_logging()

from cache import cache_stats, init_cache, purge_old_cache
from collector import collect_for_window
from constants import CACHE_RETENTION_DAYS, MAX_DAYS_TO_BACKFILL
from database import create_table_if_not_exists
from exporter import export_current_patch_to_site
from riot_api import RiotAuthError
from state import get_last_success_date, mark_day_complete


def plan_days_to_collect(today, last_success):
    """
    Which days still need collecting. Returns a list, possibly empty when
    we're already up to date.

    Split out from run() so the backfill logic can be tested without
    touching the network or the database.
    """
    yesterday = today - timedelta(days=1)

    if last_success is None:
        return [yesterday]

    days_missing = (yesterday - last_success).days

    if days_missing > MAX_DAYS_TO_BACKFILL:
        log.warning("Missed %s days (limit is %s). Not backfilling - collecting "
                    "yesterday only, history will have a gap.", days_missing, MAX_DAYS_TO_BACKFILL)
        return [yesterday]

    if days_missing > 1:
        days = [last_success + timedelta(days=d) for d in range(1, days_missing + 1)]
        log.warning("Missed %s days, backfilling each one: %s", days_missing, days)
        return days

    if last_success >= yesterday:
        log.info("Yesterday is already collected. Nothing to do.")
        return []

    return [yesterday]


def run():
    create_table_if_not_exists()
    init_cache()
    purge_old_cache(CACHE_RETENTION_DAYS)

    count, total_bytes = cache_stats()
    log.info("Cache: %s matches, %.1f MB", count, total_bytes / 1024 / 1024)

    days_to_collect = plan_days_to_collect(datetime.now().date(), get_last_success_date())
    if not days_to_collect:
        return

    for day in days_to_collect:
        window_start_dt = datetime.combine(day, datetime.min.time())
        window_end_dt = window_start_dt + timedelta(days=1)

        log.info("--- Collecting %s ---", day)

        if collect_for_window(window_start_dt, window_end_dt, day):
            mark_day_complete(day)
        else:
            log.error("Collection for %s failed, stopping (won't try later days).", day)
            break

    export_current_patch_to_site()

    log.info("Worker finished.")


if __name__ == "__main__":
    try:
        run()
    except RiotAuthError as e:
        # Exit non-zero so Task Scheduler shows a failure instead of a
        # green tick over a run that collected nothing.
        log.critical("%s", e)
        raise SystemExit(1)
    except Exception:
        # Anything unexpected: get the traceback into the log file, not just
        # onto a console nobody is watching.
        log.exception("Unhandled error, worker stopped")
        raise SystemExit(1)