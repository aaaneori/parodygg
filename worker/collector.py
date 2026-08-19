"""
Collection pipeline: match details in, aggregated daily rows out.

No HTTP or SQL specifics live here - it asks riot_api for data and hands
finished rows to database. That separation is what makes the aggregation
logic testable with plain dicts.
"""

import logging

from database import EXTENDED_STAT_COLUMNS, insert_daily_stats
from ddragon import get_champion_id_to_name_map
from constants import NAME_FIXES
from riot_api import (
    get_gm_challenger_players,
    get_match_detail,
    get_match_ids_for_player,
    get_patch_from_version,
)

log = logging.getLogger('worker')


def extract_extended_stats(participant, game_duration_seconds):
    """
    Per-match extended stats for one participant. Everything comes from the
    match detail we already fetched - no Timeline API call.

    "/min" values are computed here, against this match's own duration. The
    DB stores the sum of those ratios and divides by games on read.
    """
    # killParticipation and teamDamagePercentage come back as 0..1 fractions
    # (0.583), not percentages - hence the * 100. Worth eyeballing once
    # against real data; if Riot ever returns 58.3 directly, drop the * 100.
    challenges = participant.get("challenges", {})
    game_minutes = game_duration_seconds / 60 if game_duration_seconds > 0 else 0

    total_dmg = participant.get("totalDamageDealtToChampions", 0)
    cs = participant.get("totalMinionsKilled", 0) + participant.get("neutralMinionsKilled", 0)
    gold = participant.get("goldEarned", 0)
    vision = participant.get("visionScore", 0)

    return {
        "kills": participant.get("kills", 0),
        "deaths": participant.get("deaths", 0),
        "assists": participant.get("assists", 0),
        "solo_kills": challenges.get("soloKills", 0),
        "kill_participation_pct": challenges.get("killParticipation", 0) * 100,
        "first_bloods": 1 if participant.get("firstBloodKill") else 0,
        "physical_dmg": participant.get("physicalDamageDealtToChampions", 0),
        "magic_dmg": participant.get("magicDamageDealtToChampions", 0),
        "dmg_taken": participant.get("totalDamageTaken", 0),
        "team_dmg_pct": challenges.get("teamDamagePercentage", 0) * 100,
        "dmg_per_min": (total_dmg / game_minutes) if game_minutes > 0 else 0,
        "cs_per_min": (cs / game_minutes) if game_minutes > 0 else 0,
        "gold_per_min": (gold / game_minutes) if game_minutes > 0 else 0,
        "vision_per_min": (vision / game_minutes) if game_minutes > 0 else 0,
        "control_wards": challenges.get("controlWardsPlaced", 0)
    }


def _new_patch_bucket():
    return {"champion_stats": {}, "champion_bans": {}, "matches_processed": 0}


def process_matches(unique_match_ids):
    """
    Aggregate a set of matches, keeping each patch separate.

    Returns {patch: {champion_stats, champion_bans, matches_processed}}.

    On patch-release day a single collection window legitimately spans two
    versions: EUW plays the old patch until ranked queues go down (~03:30),
    then the new one once maintenance ends. An earlier version locked onto
    whichever patch the first match happened to carry and silently dropped
    the rest - and since match ids arrive as a set, that could just as easily
    discard the 17-hour half as the 3-hour one.
    """
    by_patch = {}
    skipped_no_position = 0
    skipped_bad_version = 0
    cache_hits = 0

    total_to_process = len(unique_match_ids)

    for index, match_id in enumerate(unique_match_ids):
        if (index + 1) % 50 == 0:
            log.info("Processed %s/%s matches...", index + 1, total_to_process)

        detail, from_cache = get_match_detail(match_id)
        if from_cache:
            cache_hits += 1
        if detail is None:
            continue

        try:
            # The whole per-match block is wrapped: one malformed match used
            # to kill the entire day's run, losing hundreds already processed.
            game_version = detail.get("info", {}).get("gameVersion")
            patch = get_patch_from_version(game_version)

            if patch is None:
                # Can't tell which patch this is, and guessing would put the
                # match in the wrong bucket - skip it.
                skipped_bad_version += 1
                continue

            bucket = by_patch.setdefault(patch, _new_patch_bucket())
            champion_stats = bucket["champion_stats"]
            champion_bans = bucket["champion_bans"]
            bucket["matches_processed"] += 1

            game_duration_seconds = detail.get("info", {}).get("gameDuration", 0)

            for participant in detail["info"]["participants"]:
                champion = participant["championName"]
                champion = NAME_FIXES.get(champion, champion)
                win = participant["win"]
                role = participant.get("teamPosition", "")

                # Riot occasionally (~0.9% of matches) can't pin down a
                # player's lane and sends an empty teamPosition. Skip them
                # rather than guess and skew the role split.
                if not role:
                    skipped_no_position += 1
                    continue

                key = (champion, role)
                if key not in champion_stats:
                    champion_stats[key] = {"games": 0, "wins": 0}
                    # Zero everything up front so we can just += below.
                    for col in EXTENDED_STAT_COLUMNS:
                        champion_stats[key][col] = 0

                champion_stats[key]["games"] += 1
                if win:
                    champion_stats[key]["wins"] += 1

                extended = extract_extended_stats(participant, game_duration_seconds)
                for col, value in extended.items():
                    champion_stats[key][col] += value

            for team in detail["info"]["teams"]:
                for ban in team["bans"]:
                    champion_id = ban["championId"]
                    if champion_id == -1:
                        continue

                    if champion_id not in champion_bans:
                        champion_bans[champion_id] = 0
                    champion_bans[champion_id] += 1

        except (KeyError, IndexError, TypeError) as e:
            # Unexpected shape - skip this match, keep everything else.
            log.warning("Skipped match %s, bad data: %s", match_id, e)
            continue

    if total_to_process > 0:
        log.info("Cache hits: %s/%s (%.0f%%)", cache_hits, total_to_process,
                 cache_hits / total_to_process * 100)

    if skipped_no_position > 0:
        log.info("Skipped participants with no lane assigned: %s", skipped_no_position)

    if skipped_bad_version > 0:
        log.warning("Skipped matches with unusable gameVersion: %s", skipped_bad_version)

    if len(by_patch) > 1:
        # Patch-release day. Worth a line in the log: it's the one day where a
        # single date produces two rows, and where match counts look odd.
        log.info("Window spans %s patches: %s", len(by_patch),
                 ", ".join(f"{p} ({b['matches_processed']} matches)"
                           for p, b in sorted(by_patch.items())))

    return by_patch


def build_rows(champion_stats, champion_bans, id_to_name):
    """
    Shape the day's aggregates for insertion: stat rows per (champion, role),
    ban rows per champion. Bans banned-but-never-picked champions just end up
    in ban_rows with no stat row - no pseudo-role needed anymore.
    """
    stat_rows = []
    for (champion, role), role_stats in sorted(champion_stats.items()):
        row = {
            "champion": champion,
            "role": role,
            "games": role_stats["games"],
            "wins": role_stats["wins"],
        }
        for col in EXTENDED_STAT_COLUMNS:
            row[col] = role_stats.get(col, 0)
        stat_rows.append(row)

    ban_rows = [
        {"champion": id_to_name[champ_id], "bans": bans}
        for champ_id, bans in sorted(champion_bans.items())
        if champ_id in id_to_name
    ]

    return stat_rows, ban_rows


def collect_for_window(window_start_dt, window_end_dt, target_date):
    """
    One full collection pass over a time window, stored under target_date.
    Used both for the normal daily run and for each day of a backfill.

    Writes one (date, patch) run per patch seen in the window - normally one,
    two on patch-release day. Returns False if nothing could be collected.
    """
    window_start = int(window_start_dt.timestamp())
    window_end = int(window_end_dt.timestamp())

    puuids = get_gm_challenger_players()
    log.info("GM+Challenger players: %s", len(puuids))

    unique_match_ids = set()
    for i, puuid in enumerate(puuids):
        match_ids = get_match_ids_for_player(puuid, window_start, window_end)
        unique_match_ids.update(match_ids)
        if (i + 1) % 25 == 0:
            log.info("[%s/%s] unique matches so far: %s", i + 1, len(puuids), len(unique_match_ids))

    log.info("Unique matchIds for %s: %s", target_date, len(unique_match_ids))

    by_patch = process_matches(unique_match_ids)

    if not by_patch:
        log.error("No matches collected for %s, nothing written.", target_date)
        return False

    id_to_name = get_champion_id_to_name_map()
    date_str = target_date.strftime('%Y-%m-%d')

    for patch, bucket in sorted(by_patch.items()):
        stat_rows, ban_rows = build_rows(
            bucket["champion_stats"], bucket["champion_bans"], id_to_name)

        insert_daily_stats(stat_rows, ban_rows, date_str, patch,
                           bucket["matches_processed"])
        log.info("Written: %s, patch %s, %s matches, %s stat rows, %s ban rows",
                 target_date, patch, bucket["matches_processed"],
                 len(stat_rows), len(ban_rows))

    return True

