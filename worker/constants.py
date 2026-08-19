"""
Config. Secrets and machine-specific stuff go in .env, everything else
lives here as plain constants.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Anchor paths to this file, not to the working directory. Task Scheduler
# can start us in System32, and then nothing gets found.
BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / '.env')


def _require(name):
    # Fail loudly. A missing key would otherwise become a None in the request
    # URL, a 403 from Riot, and a very confusing "0 matches collected".
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Variable {name} not found. "
            f"Copy .env.example to .env and fill in the values. "
            f"Expected file path: {BASE_DIR / '.env'}"
        )
    return value


# --- from .env ---

API_KEY = _require('RIOT_API_KEY')

# Git repo the worker pushes JSON exports to. Has a username in it.
SITE_FOLDER = _require('SITE_FOLDER')


# --- project config, same for anyone running this ---

REGION = 'euw1'
QUEUE = 'RANKED_SOLO_5x5'
MATCH_REGION = 'europe'

# Same queue as QUEUE, but numeric - league-v4 wants the name, match-v5 wants
# the id. Kept together so they can't drift.
QUEUE_ID = 420

DB_FILE = str(BASE_DIR / 'champions.db')
LAST_RUN_FILE = str(BASE_DIR / 'last_run.json')
LOG_FILE = str(BASE_DIR / 'worker.log')

# Raw match payloads, kept separate from champions.db on purpose: this one is
# disposable, the stats database is not.
CACHE_DB_FILE = str(BASE_DIR / 'cache.db')
CACHE_RETENTION_DAYS = 30

# Consecutive missed days we'll backfill in one run.
MAX_DAYS_TO_BACKFILL = 3

SITE_LATEST_FILENAME = 'champion_stats_latest.json'
HISTORY_FOLDER_NAME = 'history'
EXTENDED_STATS_FOLDER_NAME = 'extended_stats'

# Match-V5 says FiddleSticks, Data Dragon says Fiddlesticks.
NAME_FIXES = {
    "FiddleSticks": "Fiddlesticks",
}