"""
Aggregation of match details into daily rows.

These are the tests that matter most: an error here produces numbers that
look entirely plausible and are simply wrong.
"""

import pytest

import collector
from conftest import make_match, make_participant


def run_matches(monkeypatch, matches):
    """Feed process_matches a fixed set of payloads, no network involved."""
    by_id = {f"M{i}": m for i, m in enumerate(matches)}
    monkeypatch.setattr(collector, 'get_match_detail',
                        lambda mid: (by_id.get(mid), False))
    return collector.process_matches(list(by_id.keys()))


def single_patch(by_patch):
    """Unwrap a result that should only contain one patch."""
    assert len(by_patch) == 1, f"expected one patch, got {sorted(by_patch)}"
    patch, bucket = next(iter(by_patch.items()))
    return (bucket["champion_stats"], bucket["champion_bans"],
            bucket["matches_processed"], patch)


# --- bans -------------------------------------------------------------

def test_bans_are_not_doubled_when_champion_has_two_roles(monkeypatch):
    """
    A champion played in two roles must still be banned the number of times
    they were actually banned. Bans belong to the champion, not the role.
    """
    match = make_match(
        participants=[
            make_participant("Ahri", "MIDDLE"),
            make_participant("Ahri", "TOP"),
        ],
        bans=[103],
    )
    stats, bans, _, _ = single_patch(run_matches(monkeypatch, [match]))

    assert ("Ahri", "MIDDLE") in stats
    assert ("Ahri", "TOP") in stats
    assert bans[103] == 1, "one ban stays one ban regardless of role count"


def test_empty_ban_slot_is_ignored(monkeypatch):
    """championId -1 means nobody banned in that slot."""
    match = make_match(participants=[], bans=[103, -1, 62, -1])
    _, bans, _, _ = single_patch(run_matches(monkeypatch, [match]))

    assert set(bans) == {103, 62}, "-1 is an empty slot, not a champion"


# --- roles ------------------------------------------------------------

def test_participant_without_lane_is_skipped(monkeypatch):
    """
    Riot sometimes can't determine a lane and sends an empty teamPosition.
    Guessing would skew the role split, so those participants are dropped.
    """
    match = make_match(participants=[
        make_participant("Ahri", "MIDDLE"),
        make_participant("Zed", ""),
    ])
    stats, _, _, _ = single_patch(run_matches(monkeypatch, [match]))

    assert ("Ahri", "MIDDLE") in stats
    assert not any(champ == "Zed" for champ, _ in stats)


def test_name_fixes_applied_to_picks(monkeypatch):
    """
    Match-V5 says FiddleSticks, Data Dragon says Fiddlesticks. Picks go
    through NAME_FIXES; bans arrive as ids and are resolved via Data Dragon.
    If these two ever disagree, a champion's history splits across two files.
    """
    match = make_match(participants=[make_participant("FiddleSticks", "JUNGLE")])
    stats, _, _, _ = single_patch(run_matches(monkeypatch, [match]))

    assert ("Fiddlesticks", "JUNGLE") in stats
    assert ("FiddleSticks", "JUNGLE") not in stats


# --- robustness -------------------------------------------------------

@pytest.mark.parametrize("version", ["", None, "26", "garbage"])
def test_unusable_game_version_skips_only_that_match(monkeypatch, version):
    """One bad match used to take down the whole day's collection."""
    good = make_match(participants=[make_participant()])
    bad = make_match(participants=[make_participant()], version=version)

    stats, _, total, patch = single_patch(run_matches(monkeypatch, [good, bad]))

    assert patch == "26.16"
    assert total == 1, "the good match survives, the bad one is skipped"


def test_malformed_match_does_not_abort_the_run(monkeypatch):
    """A participant missing a required key must not lose the other matches."""
    broken = make_match(participants=[{"championName": "Ahri"}])  # no "win"
    good = make_match(participants=[make_participant("Zed", "MIDDLE")])

    stats, _, _, _ = single_patch(run_matches(monkeypatch, [broken, good]))

    assert ("Zed", "MIDDLE") in stats


def test_missing_challenges_object_does_not_crash():
    """challenges is optional in the payload; absent means zeros, not an error."""
    participant = make_participant()
    del participant["challenges"]

    extended = collector.extract_extended_stats(participant, 1800)

    assert extended["solo_kills"] == 0
    assert extended["kill_participation_pct"] == 0
    assert extended["kills"] == 6, "fields outside challenges still work"


# --- per-match ratios -------------------------------------------------

def test_per_minute_values_use_this_match_duration():
    """
    /min metrics are computed per match and summed; the database divides by
    games on read. That yields mean(per-match ratio), which is deliberately
    not the same as total damage over total minutes.
    """
    participant = make_participant(totalDamageDealtToChampions=30000)
    extended = collector.extract_extended_stats(participant, 1800)  # 30 min

    assert extended["dmg_per_min"] == pytest.approx(1000)


def test_zero_duration_does_not_divide_by_zero():
    """Remakes and aborted games report gameDuration 0."""
    extended = collector.extract_extended_stats(make_participant(), 0)

    assert extended["dmg_per_min"] == 0
    assert extended["cs_per_min"] == 0


def test_challenge_fractions_are_scaled_to_percent():
    """
    Riot returns killParticipation as 0..1. If that ever changes to 0..100,
    this test fails loudly instead of the site quietly showing 5800%.
    """
    extended = collector.extract_extended_stats(make_participant(), 1800)

    assert extended["kill_participation_pct"] == pytest.approx(60.0)
    assert extended["team_dmg_pct"] == pytest.approx(28.0)


def test_cs_combines_lane_and_jungle_minions():
    participant = make_participant(totalMinionsKilled=150, neutralMinionsKilled=30)
    extended = collector.extract_extended_stats(participant, 1800)  # 30 min

    assert extended["cs_per_min"] == pytest.approx(6.0)


# --- patch boundary ---------------------------------------------------

def test_both_patches_are_kept_on_release_day(monkeypatch):
    """
    A collection window can legitimately span two patches: EUW plays the old
    one until ranked queues go down, the new one after maintenance. Both are
    collected; an earlier version locked onto whichever patch happened to
    come first and dropped the rest.
    """
    matches = [
        make_match(participants=[make_participant()], version="26.16.1.1"),
        make_match(participants=[make_participant()], version="26.17.1.1"),
        make_match(participants=[make_participant()], version="26.17.1.1"),
    ]
    by_patch = run_matches(monkeypatch, matches)

    assert sorted(by_patch) == ["26.16", "26.17"]
    assert by_patch["26.16"]["matches_processed"] == 1
    assert by_patch["26.17"]["matches_processed"] == 2


def test_stats_do_not_leak_between_patches(monkeypatch):
    """Each patch keeps its own champion rows, games and bans."""
    matches = [
        make_match(participants=[make_participant("Ahri", "MIDDLE")],
                   bans=[103], version="26.16.1.1"),
        make_match(participants=[make_participant("Zed", "MIDDLE")],
                   bans=[238], version="26.17.1.1"),
    ]
    by_patch = run_matches(monkeypatch, matches)

    old, new = by_patch["26.16"], by_patch["26.17"]

    assert ("Ahri", "MIDDLE") in old["champion_stats"]
    assert ("Ahri", "MIDDLE") not in new["champion_stats"]
    assert ("Zed", "MIDDLE") in new["champion_stats"]
    assert old["champion_bans"] == {103: 1}
    assert new["champion_bans"] == {238: 1}


def test_unusable_version_does_not_create_a_patch_bucket(monkeypatch):
    """A match we can't place must not become a patch of its own."""
    matches = [
        make_match(participants=[make_participant()], version="26.16.1.1"),
        make_match(participants=[make_participant()], version="garbage"),
    ]
    by_patch = run_matches(monkeypatch, matches)

    assert sorted(by_patch) == ["26.16"]
    assert by_patch["26.16"]["matches_processed"] == 1


# --- build_rows -------------------------------------------------------

def test_build_rows_separates_stats_from_bans():
    champion_stats = {
        ("Ahri", "MIDDLE"): {"games": 10, "wins": 6, "kills": 50},
    }
    champion_bans = {103: 7, 1: 3}
    id_to_name = {103: "Ahri", 1: "Annie"}

    stat_rows, ban_rows = collector.build_rows(champion_stats, champion_bans, id_to_name)

    assert stat_rows == [{
        "champion": "Ahri", "role": "MIDDLE", "games": 10, "wins": 6,
        **{c: (50 if c == "kills" else 0) for c in collector.EXTENDED_STAT_COLUMNS},
    }]
    assert {r["champion"]: r["bans"] for r in ban_rows} == {"Ahri": 7, "Annie": 3}


def test_banned_but_never_picked_champion_keeps_its_bans():
    """No stat row, but the bans must not disappear."""
    stat_rows, ban_rows = collector.build_rows({}, {1: 42}, {1: "Annie"})

    assert stat_rows == []
    assert ban_rows == [{"champion": "Annie", "bans": 42}]


def test_unknown_champion_id_is_dropped_rather_than_crashing():
    """A champion released after our Data Dragon snapshot has no name yet."""
    _, ban_rows = collector.build_rows({}, {999: 5}, {1: "Annie"})

    assert ban_rows == []
