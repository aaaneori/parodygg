"""
Riot API client: retries, rate limiting, error classification.

Match details go through the cache transparently - callers don't know or
care whether a payload came off disk or over the wire.
"""

import logging
import time

import requests

from cache import cache_match, get_cached_match
from constants import API_KEY, MATCH_REGION, QUEUE, QUEUE_ID, REGION

log = logging.getLogger('worker')


class RiotAuthError(Exception):
    """API key rejected. Fatal - no point continuing the run."""


def safe_get(url, max_retries=5):
    for attempt in range(max_retries):
        response = requests.get(url)

        if response.status_code == 200:
            time.sleep(1.3)
            return response.json()

        # 401/403 means the key is invalid, expired, or lacks access. Retrying
        # won't help and every later request fails the same way - better to
        # stop loudly than to spend an hour "collecting" zero matches.
        if response.status_code in (401, 403):
            raise RiotAuthError(
                f"Riot rejected the API key (HTTP {response.status_code}). "
                f"Check RIOT_API_KEY in .env - dev keys expire every 24h."
            )

        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 5))
            log.warning("Rate limited, waiting %ss...", retry_after)
            time.sleep(retry_after + 1)
            continue

        # Riot's side is having a moment - back off and try again.
        if response.status_code >= 500:
            wait = 2 ** attempt
            log.warning("Server error %s, retry in %ss: %s", response.status_code, wait, url)
            time.sleep(wait)
            continue

        # 404 and friends: this one resource is unavailable, skip it.
        log.warning("Error %s for %s", response.status_code, url)
        time.sleep(1.3)
        return None

    log.error("Gave up after %s attempts: %s", max_retries, url)
    return None


def get_patch_from_version(game_version):
    """
    None when gameVersion is malformed. Callers skip the match instead of
    crashing - one bad match used to take down the whole day's collection.
    """
    if not game_version:
        return None

    parts = game_version.split('.')
    if len(parts) < 2:
        return None

    return parts[0] + "." + parts[1]


def get_gm_challenger_players():
    gm_url = f"https://{REGION}.api.riotgames.com/lol/league/v4/grandmasterleagues/by-queue/{QUEUE}?api_key={API_KEY}"
    gm_data = safe_get(gm_url)
    gm_players = gm_data["entries"] if gm_data else []

    ch_url = f"https://{REGION}.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/{QUEUE}?api_key={API_KEY}"
    ch_data = safe_get(ch_url)
    ch_players = ch_data["entries"] if ch_data else []

    all_players = gm_players + ch_players
    return [p["puuid"] for p in all_players]


def get_match_ids_for_player(puuid, start_time, end_time):
    url = (
        f"https://{MATCH_REGION}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
        f"?startTime={start_time}&endTime={end_time}&queue={QUEUE_ID}&count=100&api_key={API_KEY}"
    )
    result = safe_get(url)
    if result is None:
        return []
    return result


def get_match_detail(match_id):
    """Returns (detail, from_cache). detail is None if the fetch failed."""
    cached = get_cached_match(match_id)
    if cached is not None:
        return cached, True

    url = f"https://{MATCH_REGION}.api.riotgames.com/lol/match/v5/matches/{match_id}?api_key={API_KEY}"
    detail = safe_get(url)

    if detail is not None:
        cache_match(match_id, detail)

    return detail, False