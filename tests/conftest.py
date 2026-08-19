"""
Shared test setup.

Two things happen here: the project root goes on sys.path so tests can import
the modules directly, and every test that touches the database gets its own
throwaway file - the real champions.db is never opened.
"""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# constants.py refuses to import without these, and importing it is
# unavoidable - every module pulls config from it.
os.environ.setdefault('RIOT_API_KEY', 'test-key')
os.environ.setdefault('SITE_FOLDER', str(ROOT / 'tests' / '_site_output'))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """
    An empty database with the real schema, in a temp directory.

    DB_FILE is read at call time inside get_connection(), so patching the
    module attribute is enough - no need to touch the environment.
    """
    import database

    db_file = str(tmp_path / 'test.db')
    monkeypatch.setattr(database, 'DB_FILE', db_file)
    database.create_table_if_not_exists()
    return db_file


@pytest.fixture
def temp_cache(tmp_path, monkeypatch):
    import cache

    monkeypatch.setattr(cache, 'CACHE_DB_FILE', str(tmp_path / 'cache.db'))
    cache.init_cache()
    return cache


def make_participant(champion="Ahri", role="MIDDLE", win=True, **overrides):
    """A Match-V5 participant with every field the collector reads."""
    p = {
        "championName": champion,
        "teamPosition": role,
        "win": win,
        "kills": 6, "deaths": 3, "assists": 9,
        "firstBloodKill": False,
        "physicalDamageDealtToChampions": 2000,
        "magicDamageDealtToChampions": 18000,
        "totalDamageDealtToChampions": 20000,
        "totalDamageTaken": 15000,
        "totalMinionsKilled": 200,
        "neutralMinionsKilled": 20,
        "goldEarned": 12000,
        "visionScore": 30,
        "challenges": {
            "soloKills": 2,
            "killParticipation": 0.6,      # Riot sends 0..1, not 0..100
            "teamDamagePercentage": 0.28,
            "controlWardsPlaced": 3,
        },
    }
    p.update(overrides)
    return p


def make_match(participants=None, bans=None, version="26.16.1.1", duration=1800):
    """A Match-V5 detail payload trimmed to the fields the collector touches."""
    if participants is None:
        participants = [make_participant()]
    if bans is None:
        bans = []
    return {
        "info": {
            "gameVersion": version,
            "gameDuration": duration,
            "participants": participants,
            "teams": [{"bans": [{"championId": b} for b in bans]}],
        }
    }
