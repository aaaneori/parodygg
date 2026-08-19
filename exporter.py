"""
Turns database contents into the JSON files the site serves, then pushes
them. Nothing here touches the Riot API - it's pure database-to-disk.
"""

import json
import logging
import os
import subprocess
from datetime import datetime

from constants import (
    EXTENDED_STATS_FOLDER_NAME,
    HISTORY_FOLDER_NAME,
    REGION,
    SITE_FOLDER,
    SITE_LATEST_FILENAME,
)
from database import (
    get_all_champions_history,
    get_champion_extended_stats,
    get_last_updated_date,
    get_patch_summary,
    get_tracked_patches,
)
from state import get_last_known_patch, mark_patch_known

log = logging.getLogger('worker')


def export_current_patch_to_site():
    """
    Exports the newest patch in the database.

    If the patch changed since the last run, the previous
    champion_stats_latest.json is archived as champion_stats_<patch>.json and
    never touched again. The current patch is then recomputed and written to
    latest, including available_patches so the frontend can build its patch
    dropdown without a separate index file.
    """
    tracked_patches = get_tracked_patches()
    if not tracked_patches:
        log.warning("No patches in the database yet, nothing to export.")
        return

    current_patch = tracked_patches[-1]
    last_known_patch = get_last_known_patch()

    patch_changed = last_known_patch is not None and last_known_patch != current_patch

    if patch_changed:
        old_latest_path = os.path.join(SITE_FOLDER, SITE_LATEST_FILENAME)
        closed_patch_path = os.path.join(SITE_FOLDER, f'champion_stats_{last_known_patch}.json')

        if os.path.exists(old_latest_path) and not os.path.exists(closed_patch_path):
            os.rename(old_latest_path, closed_patch_path)
            log.info("Patch changed: %s -> %s. %s archived as champion_stats_%s.json.",
                     last_known_patch, current_patch, SITE_LATEST_FILENAME, last_known_patch)
        else:
            log.warning("Patch changed: %s -> %s, but nothing to archive "
                        "(no latest file, or the archive already exists).",
                        last_known_patch, current_patch)

    rows, total_matches = get_patch_summary(current_patch)
    last_updated = get_last_updated_date(current_patch)

    log.info("Exporting patch %s: %s rows, %s matches since patch start.", current_patch, len(rows), total_matches)

    export_data = {
        "region": REGION.upper(),
        "patch": current_patch,
        "available_patches": tracked_patches,
        "last_updated": last_updated,
        "total_matches": total_matches,
        "champions": rows
    }

    latest_path = os.path.join(SITE_FOLDER, SITE_LATEST_FILENAME)
    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    mark_patch_known(current_patch)

    export_champion_history_to_site()
    export_champion_extended_stats_to_site(current_patch, rows)

    push_to_github(current_patch, patch_changed, last_known_patch)


def export_champion_history_to_site():
    """
    Raw daily history per champion, one JSON file each, spanning every patch.

    One file per champion rather than a single combined one - otherwise a
    champion page would download all ~170 histories to draw one chart.

    History deliberately crosses patch boundaries so the chart stays
    continuous. Since a file can span several patches there's no single
    total_matches at file level; patch and matches_processed sit on each
    daily entry instead, which is enough to compute any period.

    Rewritten from scratch every run - one query plus a pile of small files.
    """
    history_by_champion = get_all_champions_history()

    history_folder_path = os.path.join(SITE_FOLDER, HISTORY_FOLDER_NAME)
    os.makedirs(history_folder_path, exist_ok=True)

    for champion, daily_entries in history_by_champion.items():
        export_data = {
            "champion": champion,
            "history": daily_entries
        }

        champion_file_path = os.path.join(history_folder_path, f'{champion}.json')
        with open(champion_file_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)

    log.info("Exported history for %s champions to %s/.", len(history_by_champion), HISTORY_FOLDER_NAME)


def export_champion_extended_stats_to_site(patch, patch_summary_rows):
    """
    Extended stats (KDA, damage, CS/min...) for the current patch only, one
    JSON per champion.

    Unlike history/<champion>.json, which is raw daily counts for charting,
    this holds ready-made averages keyed by role: ALL plus each role the
    champion was actually played in. Lets the sidebar follow the role toggle
    without recomputing anything client-side.

    patch_summary_rows is reused from get_patch_summary() just to know which
    (champion, role) pairs exist - saves an extra query.
    """
    extended_stats_folder_path = os.path.join(SITE_FOLDER, EXTENDED_STATS_FOLDER_NAME)
    os.makedirs(extended_stats_folder_path, exist_ok=True)

    # champion -> roles they appear in this patch, UNPICKED excluded.
    roles_by_champion = {}
    for row in patch_summary_rows:
        if row["role"] == "UNPICKED":
            continue
        roles_by_champion.setdefault(row["champion"], []).append(row["role"])

    exported_count = 0
    for champion, roles in roles_by_champion.items():
        by_role = {}

        all_stats = get_champion_extended_stats(champion, patch, role=None)
        if all_stats:
            by_role["ALL"] = all_stats

        for role in roles:
            role_stats = get_champion_extended_stats(champion, patch, role=role)
            if role_stats:
                by_role[role] = role_stats

        if not by_role:
            continue

        export_data = {
            "champion": champion,
            "patch": patch,
            "stats_by_role": by_role
        }

        champion_file_path = os.path.join(extended_stats_folder_path, f'{champion}.json')
        with open(champion_file_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        exported_count += 1

    log.info("Exported extended stats for %s champions to %s/.", exported_count, EXTENDED_STATS_FOLDER_NAME)


def push_to_github(current_patch, patch_changed=False, closed_patch=None):
    commit_message = f"Data update: patch {current_patch}, {datetime.now().strftime('%Y-%m-%d')}"

    try:
        subprocess.run(['git', 'add', SITE_LATEST_FILENAME], cwd=SITE_FOLDER, check=True)
        subprocess.run(['git', 'add', HISTORY_FOLDER_NAME], cwd=SITE_FOLDER, check=True)
        subprocess.run(['git', 'add', EXTENDED_STATS_FOLDER_NAME], cwd=SITE_FOLDER, check=True)

        if patch_changed and closed_patch:
            closed_filename = f'champion_stats_{closed_patch}.json'
            subprocess.run(['git', 'add', closed_filename], cwd=SITE_FOLDER, check=True)

        subprocess.run(['git', 'commit', '-m', commit_message], cwd=SITE_FOLDER, check=True)
        subprocess.run(['git', 'push'], cwd=SITE_FOLDER, check=True)
        log.info("Pushed to GitHub.")
    except subprocess.CalledProcessError as e:
        log.error("Git command failed: %s", e)