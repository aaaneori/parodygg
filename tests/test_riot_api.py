"""
HTTP error handling.

The point here is that different failures need different responses: a dead
key should stop the run, a missing match should not.
"""

import pytest

import riot_api


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    """Retry backoff would make these tests take half a minute."""
    monkeypatch.setattr(riot_api.time, 'sleep', lambda s: None)


def respond_with(monkeypatch, *responses):
    """Return the given responses in order, repeating the last one."""
    queue = list(responses)
    calls = []

    def fake_get(url):
        calls.append(url)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(riot_api.requests, 'get', fake_get)
    return calls


# --- error classification ---------------------------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_rejected_key_stops_the_run(monkeypatch, status):
    """
    Retrying a dead key just burns an hour collecting nothing, so this is
    the one failure that raises instead of returning None.
    """
    respond_with(monkeypatch, FakeResponse(status))

    with pytest.raises(riot_api.RiotAuthError, match="API key"):
        riot_api.safe_get("http://example.test")


def test_missing_resource_is_skipped(monkeypatch):
    """404 means this one match is unavailable; the rest of the day is fine."""
    respond_with(monkeypatch, FakeResponse(404))

    assert riot_api.safe_get("http://example.test") is None


def test_server_error_is_retried_then_given_up_on(monkeypatch):
    calls = respond_with(monkeypatch, FakeResponse(503))

    assert riot_api.safe_get("http://example.test", max_retries=3) is None
    assert len(calls) == 3


def test_server_error_recovers_if_the_retry_succeeds(monkeypatch):
    respond_with(monkeypatch, FakeResponse(500), FakeResponse(200, {"ok": True}))

    assert riot_api.safe_get("http://example.test") == {"ok": True}


def test_rate_limit_waits_and_retries(monkeypatch):
    slept = []
    monkeypatch.setattr(riot_api.time, 'sleep', lambda s: slept.append(s))
    respond_with(monkeypatch,
                 FakeResponse(429, headers={'Retry-After': '7'}),
                 FakeResponse(200, {"ok": True}))

    assert riot_api.safe_get("http://example.test") == {"ok": True}
    assert any(s >= 7 for s in slept), "Retry-After must be honoured"


# --- patch parsing ----------------------------------------------------

@pytest.mark.parametrize("game_version,expected", [
    ("26.16.123.456", "26.16"),
    ("26.16", "26.16"),
    ("14.9.1", "14.9"),
    ("", None),
    (None, None),
    ("26", None),
    ("garbage", None),
])
def test_patch_is_parsed_or_rejected(game_version, expected):
    """
    Anything unparseable returns None so the caller skips the match. This
    used to raise IndexError and take down the whole day's collection.
    """
    assert riot_api.get_patch_from_version(game_version) == expected


# --- cache integration ------------------------------------------------

def test_cached_match_skips_the_network(monkeypatch):
    monkeypatch.setattr(riot_api, 'get_cached_match', lambda mid: {"cached": True})
    monkeypatch.setattr(riot_api, 'safe_get',
                        lambda url: pytest.fail("should not hit the network"))

    detail, from_cache = riot_api.get_match_detail("EUW1_1")

    assert from_cache is True
    assert detail == {"cached": True}


def test_fetched_match_is_written_to_cache(monkeypatch):
    stored = {}
    monkeypatch.setattr(riot_api, 'get_cached_match', lambda mid: None)
    monkeypatch.setattr(riot_api, 'cache_match', lambda mid, d: stored.update({mid: d}))
    monkeypatch.setattr(riot_api, 'safe_get', lambda url: {"fresh": True})

    detail, from_cache = riot_api.get_match_detail("EUW1_2")

    assert from_cache is False
    assert stored == {"EUW1_2": {"fresh": True}}


def test_failed_fetch_is_not_cached(monkeypatch):
    """Caching a None would make the failure permanent."""
    monkeypatch.setattr(riot_api, 'get_cached_match', lambda mid: None)
    monkeypatch.setattr(riot_api, 'cache_match',
                        lambda mid, d: pytest.fail("must not cache a failure"))
    monkeypatch.setattr(riot_api, 'safe_get', lambda url: None)

    detail, _ = riot_api.get_match_detail("EUW1_3")

    assert detail is None


def test_match_id_list_falls_back_to_empty(monkeypatch):
    """A failed lookup for one player must not abort the whole sweep."""
    monkeypatch.setattr(riot_api, 'safe_get', lambda url: None)

    assert riot_api.get_match_ids_for_player("puuid", 0, 1) == []
