"""
Run state: which day we last collected, which patch was current then.

Read-modify-write the whole dict rather than each field separately - the
previous version had one function per field, each carefully preserving the
other's value, and that's exactly how fields get lost.
"""

import json
import os
from datetime import datetime

from constants import LAST_RUN_FILE


def load_state():
    """Whole state file as a dict. Empty dict if we've never run."""
    if not os.path.exists(LAST_RUN_FILE):
        return {}

    # utf-8-sig, not utf-8: editing this file by hand on Windows easily leaves
    # a BOM behind (Notepad does it, so does PowerShell's Out-File -Encoding
    # utf8), and json.load chokes on it. utf-8-sig reads both.
    with open(LAST_RUN_FILE, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def save_state(state):
    with open(LAST_RUN_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f)


def get_last_success_date():
    """Last day we know has data. None if we've never run."""
    raw = load_state().get("last_success_date")
    if not raw:
        return None
    return datetime.strptime(raw, '%Y-%m-%d').date()


def get_last_known_patch():
    """Patch that was current on the previous run. None if we've never run."""
    return load_state().get("last_known_patch")


def mark_day_complete(date_obj):
    state = load_state()
    state["last_success_date"] = date_obj.strftime('%Y-%m-%d')
    save_state(state)


def mark_patch_known(patch):
    state = load_state()
    state["last_known_patch"] = patch
    save_state(state)
