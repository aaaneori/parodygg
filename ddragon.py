"""
Data Dragon: Riot's static data CDN. Only used for the champion id -> name
map, which is needed because bans come back as numeric ids while everything
else uses names.

Cached in-process: the map is identical for every day of a backfill, and
previously it was refetched once per collected day.
"""

import logging

from riot_api import safe_get

log = logging.getLogger('worker')

_id_to_name_cache = None


def get_champion_id_to_name_map(force_refresh=False):
    global _id_to_name_cache

    if _id_to_name_cache is not None and not force_refresh:
        return _id_to_name_cache

    versions = safe_get("https://ddragon.leagueoflegends.com/api/versions.json")
    latest_version = versions[0]

    champion_data = safe_get(
        f"https://ddragon.leagueoflegends.com/cdn/{latest_version}/data/en_US/champion.json"
    )

    _id_to_name_cache = {
        int(champ_info["key"]): champ_name
        for champ_name, champ_info in champion_data["data"].items()
    }
    log.info("Data Dragon %s: %s champions", latest_version, len(_id_to_name_cache))
    return _id_to_name_cache