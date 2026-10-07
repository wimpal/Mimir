"""Play a Jellyfin movie on the TV from chat (T-116) — path A Sessions.

Deterministic resolve → M3 pending → launch_app jellyfin → Sessions PlayNow.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from brain.config import Settings, jellyfin_sync_configured
from brain.db import Database, Movie
from brain.device_inventory import DEVICE_LAUNCH_APP_TOOL, PendingDeviceStore
from brain.jellyfin_client import JellyfinClient, JellyfinError
from brain.mcp.devices import parse_devices_list
from brain.recipe_import import is_bare_cancel, is_bare_confirm
from brain.repeat_last import is_repeat_intent
from brain.tools.recommend import resolve_seed

logger = logging.getLogger("mimir.tv_play")

PENDING_TTL_S = 600.0
DEVICE_LIST_TOOL = "homebase.devices.list"
SESSION_POLL_INTERVAL_S = 1.5
SESSION_POLL_DEFAULT_MAX_S = 45.0
WEBOS_CLIENT_MARKER = "WebOS"

# Capture title between play/zet/speel and on-the-TV / op-de-TV.
_PLAY_EXTRACT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bplay\s+(.+?)\s+on\s+(?:the\s+)?(?:tv|television)\b",
        r"\bput\s+(.+?)\s+on\s+(?:the\s+)?(?:tv|television)\b",
        r"\bzet\s+(.+?)\s+op\s+de\s+(?:tv|televisie)\b",
        r"\bspeel\s+(.+?)\s+op\s+(?:de\s+)?(?:tv|televisie)\b",
    )
)

_PLAY_DETECT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bplay\b.+\bon\s+(?:the\s+)?(?:tv|television)\b",
        r"\bput\b.+\bon\s+(?:the\s+)?(?:tv|television)\b",
        r"\bzet\b.+\bop\s+de\s+(?:tv|televisie)\b",
        r"\bspeel\b.+\bop\s+(?:de\s+)?(?:tv|televisie)\b",
    )
)

# A marker word in the utterance means "series", even when a movie shares the title.
SERIES_MARKER = re.compile(
    r"\b(episode|aflevering|season|seizoen|serie|series|"
    r"next\s+episode|volgende\s+aflevering)\b",
    re.IGNORECASE,
)

# Season / episode / "next" qualifiers stripped off the captured title (T-127).
_SERIES_EXTRACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^season\s+(\d{1,2})\s+episode\s+(\d{1,3})\s+(?:of|van)\s+(.+)$",
            re.IGNORECASE,
        ),
        "season_episode",
    ),
    (
        re.compile(
            r"^seizoen\s+(\d{1,2})\s+aflevering\s+(\d{1,3})\s+van\s+(.+)$",
            re.IGNORECASE,
        ),
        "season_episode",
    ),
    (
        re.compile(r"^episode\s+(\d{1,3})\s+(?:of|van)\s+(.+)$", re.IGNORECASE),
        "episode",
    ),
    (
        re.compile(r"^aflevering\s+(\d{1,3})\s+van\s+(.+)$", re.IGNORECASE),
        "episode",
    ),
    (
        re.compile(r"^season\s+(\d{1,2})\s+(?:of|van)\s+(.+)$", re.IGNORECASE),
        "season",
    ),
    (
        re.compile(r"^seizoen\s+(\d{1,2})\s+van\s+(.+)$", re.IGNORECASE),
        "season",
    ),
    (
        re.compile(
            r"^(?:the\s+)?next\s+episode\s+(?:of|van)\s+(.+)$", re.IGNORECASE
        ),
        "next",
    ),
    (
        re.compile(
            r"^(?:de\s+)?volgende\s+aflevering\s+van\s+(.+)$", re.IGNORECASE
        ),
        "next",
    ),
)

_PLAY_NEGATION = re.compile(
    r"\b(don'?t|do\s+not|niet|geen)\b.*\b(play|speel|zet)\b|"
    r"\b(don'?t|do\s+not|niet|geen)\b.*\b(tv|televisie)\b",
    re.IGNORECASE,
)

_PLAY_QUESTION = re.compile(
    r"\b(what|which|hoe|wat|welke|explain|leg\s+uit)\b|"
    r"\?\s*$",
    re.IGNORECASE,
)

_OPEN_APP_ONLY_TITLE = frozenset({"jellyfin"})

# A captured "title" that is only a device word is never a media resolve.
_DEVICE_WORD_TITLE = frozenset(
    {
        "tv",
        "the tv",
        "de tv",
        "television",
        "the television",
        "televisie",
        "home",
        "the home",
        "hdmi1",
        "hdmi 1",
        "hdmi2",
        "hdmi 2",
        "hdmi3",
        "hdmi 3",
        "hdmi4",
        "hdmi 4",
    }
)


@dataclass
class SeriesRequest:
    """Series title + optional episode numbers parsed from one utterance."""

    title: str
    season: int | None = None
    episode: int | None = None
    next_episode: bool = False


@dataclass
class Playable:
    """A resolved media item (series) usable in the disambiguation list."""

    jellyfin_id: str
    title: str
    year: int | None = None
    kind: str = "series"


def title_match_quality(query: str, title: str) -> str:
    """exact | contains | none — the same order resolve_seed uses.

    Lets a movie resolve and a live series resolve be ranked against each
    other, so an exact series title beats a substring movie hit.
    """
    needle = (query or "").strip().casefold()
    hay = (title or "").strip().casefold()
    if not needle or not hay:
        return "none"
    if hay == needle:
        return "exact"
    if needle in hay:
        return "contains"
    return "none"


@dataclass
class PendingPlayOnTv:
    device_id: str
    device_name: str
    jellyfin_id: str
    title: str
    year: int | None
    sync_generation: int | None
    wake_if_needed: bool
    created_monotonic: float = field(default_factory=time.monotonic)
    confirmable: bool = True
    dutch: bool = False
    media_kind: str = "movie"
    series_id: str | None = None
    episode_id: str | None = None
    episode_number: int | None = None
    season_number: int | None = None
    episode_title: str | None = None


@dataclass
class PendingPlayDisambiguation:
    query: str
    candidates: list[dict[str, Any]]
    created_monotonic: float = field(default_factory=time.monotonic)
    dutch: bool = False
    kind: str = "movie"
    season: int | None = None
    episode: int | None = None


class PendingPlayStore:
    """In-memory pending play-on-TV plans + title disambiguation."""

    def __init__(self, *, ttl_s: float = PENDING_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._play: dict[str, PendingPlayOnTv] = {}
        self._ambiguous: dict[str, PendingPlayDisambiguation] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        for store in (self._play, self._ambiguous):
            expired = [
                key
                for key, item in store.items()
                if now - item.created_monotonic > self._ttl_s
            ]
            for key in expired:
                del store[key]

    def set_play(self, conversation_id: str, pending: PendingPlayOnTv) -> None:
        self._purge_expired()
        self._ambiguous.pop(conversation_id, None)
        self._play[conversation_id] = pending

    def set_ambiguous(
        self, conversation_id: str, pending: PendingPlayDisambiguation
    ) -> None:
        self._purge_expired()
        self._play.pop(conversation_id, None)
        self._ambiguous[conversation_id] = pending

    def get_play(self, conversation_id: str | None) -> PendingPlayOnTv | None:
        if not conversation_id:
            return None
        self._purge_expired()
        return self._play.get(conversation_id)

    def get_ambiguous(
        self, conversation_id: str | None
    ) -> PendingPlayDisambiguation | None:
        if not conversation_id:
            return None
        self._purge_expired()
        return self._ambiguous.get(conversation_id)

    def has_play(self, conversation_id: str | None) -> bool:
        return self.get_play(conversation_id) is not None

    def has_ambiguous(self, conversation_id: str | None) -> bool:
        return self.get_ambiguous(conversation_id) is not None

    def is_confirmable(self, conversation_id: str | None) -> bool:
        item = self.get_play(conversation_id)
        return bool(item and item.confirmable)

    def is_dutch(self, conversation_id: str | None) -> bool:
        play = self.get_play(conversation_id)
        if play is not None:
            return play.dutch
        amb = self.get_ambiguous(conversation_id)
        return bool(amb and amb.dutch)

    def clear(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        self._play.pop(conversation_id, None)
        self._ambiguous.pop(conversation_id, None)

    def clear_play(self, conversation_id: str | None) -> PendingPlayOnTv | None:
        if not conversation_id:
            return None
        return self._play.pop(conversation_id, None)


def user_message_requests_play_on_tv(text: str) -> bool:
    if not (text or "").strip():
        return False
    if _PLAY_NEGATION.search(text) or _PLAY_QUESTION.search(text):
        return False
    title = extract_play_title(text)
    if title is None:
        return False
    if title.casefold().strip() in _OPEN_APP_ONLY_TITLE:
        return False
    if title.casefold().strip() in _DEVICE_WORD_TITLE:
        return False
    return any(p.search(text) for p in _PLAY_DETECT_PATTERNS)


def extract_play_title(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    for pat in _PLAY_EXTRACT_PATTERNS:
        m = pat.search(raw)
        if not m:
            continue
        title = (m.group(1) or "").strip(" \t\"'`.,")
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            return title
    return None


_SERIES_TITLE_PREFIX = re.compile(r"^(?:the\s+|de\s+)?(?:series|serie)\s+", re.IGNORECASE)


def extract_series_request(text: str) -> SeriesRequest | None:
    """Series + optional season/episode from a play-on-TV phrase (T-127).

    None unless the utterance carries a series marker, so a plain
    "play <title> on the TV" still resolves as a movie first.
    """
    if not user_message_requests_play_on_tv(text):
        return None
    if not SERIES_MARKER.search(text):
        return None
    raw = extract_play_title(text)
    if raw is None:
        return None
    for pat, kind in _SERIES_EXTRACT_PATTERNS:
        m = pat.match(raw)
        if not m:
            continue
        title = re.sub(r"\s+", " ", m.groups()[-1].strip(" \t\"'`.,"))
        if not title:
            return None
        if kind == "season_episode":
            return SeriesRequest(
                title=title, season=int(m.group(1)), episode=int(m.group(2))
            )
        if kind == "episode":
            return SeriesRequest(title=title, episode=int(m.group(1)))
        if kind == "season":
            return SeriesRequest(title=title, season=int(m.group(1)))
        return SeriesRequest(title=title, next_episode=True)
    title = _SERIES_TITLE_PREFIX.sub("", raw).strip(" \t\"'`.,")
    return SeriesRequest(title=title) if title else None


def _playable_from_item(raw: dict[str, Any]) -> Playable | None:
    item_id = str(raw.get("Id") or "").strip()
    name = str(raw.get("Name") or "").strip()
    if not item_id or not name:
        return None
    year_raw = raw.get("ProductionYear")
    try:
        year = int(year_raw) if year_raw is not None else None
    except (TypeError, ValueError):
        year = None
    return Playable(jellyfin_id=item_id, title=name, year=year)


def resolve_series_for_play(
    client: JellyfinClient,
    *,
    title: str,
) -> tuple[Playable | None, list[Playable] | None, bool]:
    """Same match order as resolve_seed: exact, unique contains, else ambiguous."""
    needle = (title or "").strip()
    if not needle:
        return None, None, True
    raws = client.find_series(needle)
    items = [p for p in (_playable_from_item(r) for r in raws) if p is not None]
    lower = needle.casefold()

    exact = [i for i in items if i.title.casefold() == lower]
    if len(exact) == 1:
        return exact[0], None, False
    if len(exact) > 1:
        return None, exact, False

    contains = [i for i in items if lower in i.title.casefold()]
    if len(contains) == 1:
        return contains[0], None, False
    if len(contains) > 1:
        return None, contains, False
    return None, None, True


def _episode_numbers(raw: dict[str, Any]) -> tuple[int, int]:
    def _int(key: str) -> int:
        value = raw.get(key)
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    return _int("ParentIndexNumber"), _int("IndexNumber")


def _episode_played(raw: dict[str, Any]) -> bool:
    user_data = raw.get("UserData")
    if not isinstance(user_data, dict):
        # Unknown user data must not hide the only unplayed episode.
        return False
    return bool(user_data.get("Played"))


def _playable_episodes(
    episodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Episodes with a real season and episode number, season 0 = specials."""
    numbered = [e for e in episodes if all(n > 0 for n in _episode_numbers(e))]
    real_seasons = [e for e in numbered if _episode_numbers(e)[0] >= 1]
    return real_seasons or numbered


def pick_episode(
    episodes: list[dict[str, Any]],
    *,
    season: int | None = None,
    episode: int | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Pick the episode to play. Returns (episode_item | None, reason).

    Default (no season/episode named): continue after furthest watched —
    earlier unplayed holes are skipped so a series mid-run is not reset.
    Prefer ``JellyfinClient.next_up_episode`` at the call site when the
    server is reachable; this is the offline / fallback selector.

    Reasons: requested | unplayed | no-episodes | season-not-found |
    episode-not-found | all-played | caught-up.
    """
    usable = _playable_episodes(episodes)
    if not usable:
        return None, "no-episodes"

    if season is not None:
        bucket = [e for e in usable if _episode_numbers(e)[0] == season]
        if not bucket:
            return None, "season-not-found"
        if episode is not None:
            for e in bucket:
                if _episode_numbers(e)[1] == episode:
                    return e, "requested"
            return None, "episode-not-found"
        unplayed = [e for e in bucket if not _episode_played(e)]
        if not unplayed:
            return None, "all-played"
        unplayed.sort(key=lambda e: _episode_numbers(e))
        return unplayed[0], "unplayed"

    if episode is not None:
        # Episode number without a season: first season that has that number.
        for season_no in sorted({_episode_numbers(e)[0] for e in usable}):
            for e in usable:
                if _episode_numbers(e) == (season_no, episode):
                    return e, "requested"
        return None, "episode-not-found"

    # Continue after furthest played. Skipped earlier holes must not win.
    played = [e for e in usable if _episode_played(e)]
    if not played:
        usable.sort(key=lambda e: _episode_numbers(e))
        return usable[0], "unplayed"

    furthest = max(played, key=lambda e: _episode_numbers(e))
    after = [
        e
        for e in usable
        if _episode_numbers(e) > _episode_numbers(furthest)
        and not _episode_played(e)
    ]
    if after:
        after.sort(key=lambda e: _episode_numbers(e))
        return after[0], "unplayed"
    # Progress is past every later episode, but earlier skips remain.
    if any(not _episode_played(e) for e in usable):
        return None, "caught-up"
    return None, "all-played"


def episode_availability(episodes: list[dict[str, Any]]) -> str:
    """Short 'what does exist' summary for the loud episode failures."""
    usable = _playable_episodes(episodes)
    if not usable:
        return ""
    seasons = sorted({_episode_numbers(e)[0] for e in usable})
    if len(seasons) == 1:
        season = seasons[0]
        last = max(_episode_numbers(e)[1] for e in usable)
        return f"(S{season} has episodes 1-{last})"
    return f"(seasons {seasons[0]}-{seasons[-1]})"


def should_keep_pending_play(
    text: str, *, has_ambiguous: bool = False
) -> bool:
    if not (text or "").strip():
        return False
    if is_bare_confirm(text) or is_bare_cancel(text):
        return True
    if is_repeat_intent(text):
        return True
    if user_message_requests_play_on_tv(text):
        return True
    if has_ambiguous:
        raw = text.strip()
        if re.fullmatch(r"\d{1,2}", raw):
            return True
        if re.fullmatch(r"(?:19|20)\d{2}", raw):
            return True
        # Short title / "Title (year)" picks — not a new unrelated ask.
        if len(raw) <= 80 and not raw.endswith("?"):
            return True
    return False


def play_locale_dutch(user_message: str, *, prefer_dutch: bool | None = None) -> bool:
    if prefer_dutch is not None:
        return prefer_dutch
    msg = (user_message or "").lower()
    return any(
        w in msg
        for w in (
            "zet",
            "speel",
            "televisie",
            "op de tv",
            "film",
            "afspelen",
            "aflevering",
            "seizoen",
            "serie",
            "volgende aflevering",
        )
    )


def jellyfin_client_from_settings(settings: Settings) -> JellyfinClient | None:
    if not jellyfin_sync_configured(settings):
        return None
    jf = settings.jellyfin
    assert jf.api_key is not None
    return JellyfinClient(
        jf.url,
        jf.api_key,
        user_id=jf.user_id,
        page_size=jf.page_size,
        request_timeout_s=min(30.0, float(settings.timeouts.tool_s)),
    )


def match_webos_controllable_sessions(
    sessions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Spike-proven match: SupportsRemoteControl + Client contains WebOS."""
    out: list[dict[str, Any]] = []
    for sess in sessions:
        if not sess.get("SupportsRemoteControl"):
            continue
        client = str(sess.get("Client") or "")
        if WEBOS_CLIENT_MARKER not in client:
            continue
        # Never target our own API client.
        if "Mimir" in client:
            continue
        out.append(sess)
    return out


def _movie_label(title: str, year: int | None) -> str:
    if year is not None:
        return f"{title} ({year})"
    return title


def _episode_label(pending: PendingPlayOnTv) -> str:
    """``S2E4 Title`` when known, else the episode title, else nothing."""
    bits: list[str] = []
    if pending.season_number is not None and pending.episode_number is not None:
        bits.append(f"S{pending.season_number}E{pending.episode_number}")
    if pending.episode_title:
        bits.append(pending.episode_title)
    return " ".join(bits)


def _is_series(pending: PendingPlayOnTv) -> bool:
    return pending.media_kind == "series"


def build_play_confirm_reply(pending: PendingPlayOnTv) -> str:
    name = pending.device_name or pending.device_id or "TV"
    if _is_series(pending):
        label = _movie_label(pending.title, pending.year)
        ep = _episode_label(pending)
        target = f"{label} — {ep}" if ep else label
        if pending.dutch:
            return (
                f"**{target}** afspelen op **{name}** via Jellyfin — nog niet gedaan.\n"
                "Zeg *ja* of tik Confirm."
            )
        return (
            f"Play **{target}** on **{name}** via Jellyfin — not done yet.\n"
            "Say *yes* or tap Confirm."
        )
    label = _movie_label(pending.title, pending.year)
    if pending.dutch:
        return (
            f"**{label}** afspelen op **{name}** via Jellyfin — nog niet gedaan.\n"
            "Zeg *ja* of tik Confirm."
        )
    return (
        f"Play **{label}** on **{name}** via Jellyfin — not done yet.\n"
        "Say *yes* or tap Confirm."
    )


def _candidate_name(item: Any) -> str:
    name = getattr(item, "name", None) or getattr(item, "title", None) or ""
    return str(name)


def _candidate_year(item: Any) -> int | None:
    value = getattr(item, "year", None)
    return value if isinstance(value, int) else None


def build_play_ambiguous_reply(
    query: str,
    candidates: list[Any],
    *,
    dutch: bool,
    kind: str = "movie",
) -> str:
    lines: list[str] = []
    noun = "series" if kind == "series" else "movies"
    if dutch:
        lines.append(
            f"Meerdere {noun} matchen **{query}** — welke bedoel je?"
        )
    else:
        lines.append(f"Several {noun} match **{query}** — which one?")
    for i, m in enumerate(candidates[:8], start=1):
        label = _movie_label(_candidate_name(m), _candidate_year(m))
        lines.append(f"{i}. {label}")
    return "\n".join(lines)


def build_play_missing_reply(query: str, *, dutch: bool) -> str:
    if dutch:
        return f"Geen film **{query}** in de Jellyfin-catalogus."
    return f"No movie **{query}** in the Jellyfin catalogue."


def build_play_series_missing_reply(query: str, *, dutch: bool) -> str:
    if dutch:
        return f"Geen serie **{query}** in Jellyfin."
    return f"No series **{query}** in Jellyfin."


def build_play_episode_failure_reply(
    reason: str,
    title: str,
    *,
    dutch: bool,
    available: str = "",
) -> str:
    """Loud, specific failure for the episode selector (T-127)."""
    tail = f" {available}" if available else ""
    if reason == "all-played":
        if dutch:
            return (
                f"Alle afleveringen van **{title}** zijn gekeken — "
                f"welke aflevering of welk seizoen?{tail}"
            )
        return (
            f"All episodes of **{title}** are watched — "
            f"which episode or season?{tail}"
        )
    if reason == "caught-up":
        if dutch:
            return (
                f"Je bent bij met **{title}** — "
                f"noem seizoen of aflevering om een overgeslagen aflevering te kijken.{tail}"
            )
        return (
            f"You're caught up on **{title}** — "
            f"name a season or episode to jump back to a skipped one.{tail}"
        )
    if reason == "season-not-found":
        if dutch:
            return f"Seizoen niet gevonden in **{title}**.{tail}"
        return f"Season not found in **{title}**.{tail}"
    if reason == "episode-not-found":
        if dutch:
            return f"Die aflevering bestaat niet in dat seizoen van **{title}**.{tail}"
        return f"That episode does not exist in that season of **{title}**.{tail}"
    if dutch:
        return f"Geen afleveringen gevonden voor **{title}**."
    return f"No episodes found for **{title}**."


def build_play_success_reply(pending: PendingPlayOnTv) -> str:
    name = pending.device_name or "TV"
    if _is_series(pending):
        label = _movie_label(pending.title, pending.year)
        ep = _episode_label(pending)
        target = f"{label} — {ep}" if ep else label
        if pending.dutch:
            return f"**{target}** speelt nu op **{name}**."
        return f"Playing **{target}** on **{name}**."
    label = _movie_label(pending.title, pending.year)
    if pending.dutch:
        return f"**{label}** speelt nu op **{name}**."
    return f"Playing **{label}** on **{name}**."


def build_play_failure_reply(message: str, *, dutch: bool) -> str:
    msg = (message or "").strip() or (
        "afspelen mislukt" if dutch else "playback failed"
    )
    return msg


def _candidate_dicts(movies: list[Movie]) -> list[dict[str, Any]]:
    return [
        {
            "jellyfin_id": m.jellyfin_id,
            "title": m.name,
            "year": m.year,
        }
        for m in movies
    ]


def _series_candidate_dicts(items: list[Playable]) -> list[dict[str, Any]]:
    return [
        {
            "jellyfin_id": p.jellyfin_id,
            "title": p.title,
            "year": p.year,
            "kind": "series",
        }
        for p in items
    ]


def resolve_disambiguation_pick(
    text: str,
    pending: PendingPlayDisambiguation,
) -> dict[str, Any] | None:
    """Map user reply to one staged candidate, or None."""
    raw = (text or "").strip()
    if not raw or is_bare_confirm(raw) or is_bare_cancel(raw):
        return None
    candidates = pending.candidates
    if not candidates:
        return None
    # Numeric index 1..n
    if re.fullmatch(r"\d{1,2}", raw):
        idx = int(raw)
        if 1 <= idx <= len(candidates):
            return dict(candidates[idx - 1])
    lower = raw.casefold()
    # Year alone when unique among candidates
    year_m = re.fullmatch(r"(?:19|20)\d{2}", raw)
    if year_m:
        year = int(raw)
        hits = [c for c in candidates if c.get("year") == year]
        if len(hits) == 1:
            return dict(hits[0])
    # Exact / unique contains on title
    exact = [
        c
        for c in candidates
        if str(c.get("title") or "").casefold() == lower
    ]
    if len(exact) == 1:
        return dict(exact[0])
    contains = [
        c
        for c in candidates
        if lower in str(c.get("title") or "").casefold()
    ]
    if len(contains) == 1:
        return dict(contains[0])
    # "Title (year)"
    m = re.match(r"^(.+?)\s*\((\d{4})\)\s*$", raw)
    if m:
        t, y = m.group(1).strip().casefold(), int(m.group(2))
        hits = [
            c
            for c in candidates
            if str(c.get("title") or "").casefold() == t and c.get("year") == y
        ]
        if len(hits) == 1:
            return dict(hits[0])
    return None


def pick_tv_device(
    devices: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Prefer unique tv_capable device; else unique device overall."""
    capable = [d for d in devices if d.get("tv_capable") is True]
    if len(capable) == 1:
        return capable[0]
    if len(capable) > 1:
        return None
    if len(devices) == 1:
        return devices[0]
    return None


def resolve_movie_for_play(
    db: Database, title: str
) -> tuple[Movie | None, list[Movie] | None, bool]:
    """Resolve against full active catalogue (includes watched)."""
    movies = db.list_active_movies()
    return resolve_seed(movies, title)


def revalidate_movie(
    db: Database,
    *,
    jellyfin_id: str,
    sync_generation: int | None,
) -> Movie | None:
    state = db.get_sync_state()
    if (
        sync_generation is not None
        and state.active_generation is not None
        and state.active_generation != sync_generation
    ):
        # Generation rolled — still ok if id present in new active set.
        pass
    for m in db.list_active_movies():
        if m.jellyfin_id == jellyfin_id:
            return m
    return None


DispatchFn = Callable[[str, dict[str, Any], float], str]


def execute_play_on_tv(
    pending: PendingPlayOnTv,
    *,
    settings: Settings,
    db: Database,
    dispatch: DispatchFn,
    deadline_monotonic: float | None,
    pending_devices: PendingDeviceStore | None = None,
    conversation_id: str | None = None,
    jellyfin_client: JellyfinClient | None = None,
    session_poll_max_s: float = SESSION_POLL_DEFAULT_MAX_S,
) -> tuple[bool, str]:
    """Run launch_app → poll WebOS session → PlayNow. Returns (ok, user_reply)."""
    dutch = pending.dutch
    owns_client = jellyfin_client is None
    client = (
        jellyfin_client
        if jellyfin_client is not None
        else jellyfin_client_from_settings(settings)
    )
    if client is None:
        return False, build_play_failure_reply(
            "Jellyfin is not configured."
            if not dutch
            else "Jellyfin is niet geconfigureerd.",
            dutch=dutch,
        )

    def _fail(message: str) -> tuple[bool, str]:
        if owns_client:
            client.close()
        return False, build_play_failure_reply(message, dutch=dutch)

    playable_title = pending.title
    playable_year = pending.year
    if _is_series(pending):
        try:
            episode = client.get_item(pending.jellyfin_id)
        except JellyfinError as exc:
            return _fail(
                f"Jellyfin check failed: {exc}" if not dutch else f"Jellyfin-check mislukt: {exc}"
            )
        if episode is None:
            return _fail(
                "That episode is no longer available."
                if not dutch
                else "Die aflevering is niet meer beschikbaar."
            )
        playable_title = str(episode.get("SeriesName") or pending.title) or pending.title
    else:
        movie = revalidate_movie(
            db,
            jellyfin_id=pending.jellyfin_id,
            sync_generation=pending.sync_generation,
        )
        if movie is None:
            return _fail(
                "Film niet meer in de catalogus."
                if dutch
                else "Movie no longer in the catalogue.",
            )
        playable_title = movie.name
        playable_year = movie.year

    wake = pending.wake_if_needed
    if (
        wake
        and pending_devices is not None
        and pending_devices.tv_wake_recent(
            conversation_id, device_id=pending.device_id
        )
    ):
        wake = False

    launch_args: dict[str, Any] = {
        "device_id": pending.device_id,
        "target": "jellyfin",
    }
    if wake:
        launch_args["wake_if_needed"] = True

    remaining = None
    if deadline_monotonic is not None:
        remaining = max(0.0, deadline_monotonic - time.monotonic())
    launch_timeout = 120.0
    if remaining is not None:
        # Keep headroom for session poll + play.
        launch_timeout = min(launch_timeout, max(5.0, remaining - session_poll_max_s - 10.0))

    launch_raw = dispatch(DEVICE_LAUNCH_APP_TOOL, launch_args, launch_timeout)
    if launch_raw.startswith("error:") or "\"error\"" in launch_raw[:80]:
        return False, build_play_failure_reply(launch_raw, dutch=dutch)

    try:
        launch_body = json.loads(launch_raw) if launch_raw.strip().startswith("{") else {}
    except json.JSONDecodeError:
        launch_body = {}
    status = str(launch_body.get("status") or "").strip().lower()
    if status == "dry_run":
        return False, build_play_failure_reply(
            "TV launch was dry_run — playback not started."
            if not dutch
            else "TV-start was dry_run — afspelen niet gestart.",
            dutch=dutch,
        )
    if status and status not in {"ok", "woke_and_ok"}:
        return False, build_play_failure_reply(
            f"TV launch failed (status={status}).",
            dutch=dutch,
        )

    if (
        wake
        and status in {"ok", "woke_and_ok"}
        and pending_devices is not None
    ):
        pending_devices.record_tv_wake(
            conversation_id, device_id=pending.device_id
        )

    try:
        session = _wait_unique_webos_session(
            client,
            deadline_monotonic=deadline_monotonic,
            poll_max_s=session_poll_max_s,
        )
        if session is None:
            return False, build_play_failure_reply(
                "No controllable Jellyfin for WebOS session on the TV "
                "(open Jellyfin on the set, then try again)."
                if not dutch
                else "Geen bestuurbare Jellyfin-for-WebOS-sessie op de TV "
                "(open Jellyfin op de TV en probeer opnieuw).",
                dutch=dutch,
            )
        sid = str(session.get("Id") or "").strip()
        if not sid:
            return False, build_play_failure_reply(
                "Jellyfin session missing id.",
                dutch=dutch,
            )
        client.play_items(sid, [pending.jellyfin_id])
    except JellyfinError as exc:
        return False, build_play_failure_reply(
            f"Jellyfin launch ok, but playback failed: {exc}"
            if not dutch
            else f"Jellyfin gestart, maar afspelen mislukt: {exc}",
            dutch=dutch,
        )
    finally:
        if owns_client and client is not None:
            client.close()

    return True, build_play_success_reply(
        replace(pending, title=playable_title, year=playable_year)
    )


def _wait_unique_webos_session(
    client: JellyfinClient,
    *,
    deadline_monotonic: float | None,
    poll_max_s: float,
) -> dict[str, Any] | None:
    """Poll until exactly one controllable WebOS session, or give up."""
    started = time.monotonic()
    last: list[dict[str, Any]] = []
    while True:
        elapsed = time.monotonic() - started
        if elapsed >= poll_max_s:
            break
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            break
        try:
            sessions = client.list_sessions()
        except JellyfinError:
            sessions = []
        matched = match_webos_controllable_sessions(sessions)
        last = matched
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            logger.warning(
                "ambiguous WebOS sessions: %s",
                [s.get("Id") for s in matched],
            )
            return None
        time.sleep(SESSION_POLL_INTERVAL_S)
    if len(last) == 1:
        return last[0]
    return None


def stage_play_from_movie(
    *,
    movie: Movie,
    device: dict[str, Any],
    wake_if_needed: bool,
    dutch: bool,
    db: Database,
) -> PendingPlayOnTv:
    state = db.get_sync_state()
    return PendingPlayOnTv(
        device_id=str(device.get("id") or "").strip(),
        device_name=str(device.get("name") or "").strip()
        or str(device.get("id") or "TV"),
        jellyfin_id=movie.jellyfin_id,
        title=movie.name,
        year=movie.year,
        sync_generation=state.active_generation,
        wake_if_needed=wake_if_needed,
        dutch=dutch,
    )


def stage_play_from_series(
    *,
    series: Playable,
    episode: dict[str, Any],
    device: dict[str, Any],
    wake_if_needed: bool,
    dutch: bool,
    db: Database,
) -> PendingPlayOnTv:
    state = db.get_sync_state()
    episode_id = str(episode.get("Id") or "").strip()
    season, number = _episode_numbers(episode)
    return PendingPlayOnTv(
        device_id=str(device.get("id") or "").strip(),
        device_name=str(device.get("name") or "").strip()
        or str(device.get("id") or "TV"),
        jellyfin_id=episode_id,
        title=series.title,
        year=series.year,
        sync_generation=state.active_generation,
        wake_if_needed=wake_if_needed,
        dutch=dutch,
        media_kind="series",
        series_id=series.jellyfin_id,
        episode_id=episode_id or None,
        episode_number=number or None,
        season_number=season or None,
        episode_title=str(episode.get("Name") or "").strip() or None,
    )


def list_devices_via_dispatch(dispatch: DispatchFn, timeout_s: float) -> list[dict[str, Any]]:
    raw = dispatch(DEVICE_LIST_TOOL, {}, timeout_s)
    devices = parse_devices_list(raw)
    return devices or []
