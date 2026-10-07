"""Jellyfin favourite / unfavourite — early M3 path (T-130)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from brain.config import Settings
from brain.jellyfin_client import JellyfinClient, JellyfinError
from brain.recipe_import import is_bare_cancel, is_bare_confirm, is_repeat_intent
from brain.tools.media_lookup import _client_from_settings, _jellyfin_ready

PENDING_TTL_S = 600.0

ResolveStatus = Literal["ok", "ambiguous", "missing", "truncated", "error"]


@dataclass(frozen=True)
class FavouriteRequest:
    title: str
    favorite: bool
    dutch: bool


@dataclass(frozen=True)
class FavouriteCandidate:
    item_id: str
    kind: str  # movie | series
    title: str
    year: int | None


@dataclass
class PendingFavourite:
    item_id: str
    title: str
    year: int | None
    kind: str
    favorite: bool
    dutch: bool
    created_monotonic: float = field(default_factory=time.monotonic)
    confirmable: bool = True


@dataclass
class PendingFavouriteDisambiguation:
    query: str
    favorite: bool
    candidates: list[FavouriteCandidate]
    dutch: bool
    created_monotonic: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class ResolveResult:
    status: ResolveStatus
    candidates: list[FavouriteCandidate] = field(default_factory=list)
    error: str | None = None


class PendingFavouriteStore:
    """In-memory pending favourite plans + title disambiguation."""

    def __init__(self, *, ttl_s: float = PENDING_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._fav: dict[str, PendingFavourite] = {}
        self._ambiguous: dict[str, PendingFavouriteDisambiguation] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        for store in (self._fav, self._ambiguous):
            expired = [
                key
                for key, item in store.items()
                if now - item.created_monotonic > self._ttl_s
            ]
            for key in expired:
                del store[key]

    def set_favourite(self, conversation_id: str, pending: PendingFavourite) -> None:
        self._purge_expired()
        self._ambiguous.pop(conversation_id, None)
        self._fav[conversation_id] = pending

    def set_ambiguous(
        self, conversation_id: str, pending: PendingFavouriteDisambiguation
    ) -> None:
        self._purge_expired()
        self._fav.pop(conversation_id, None)
        self._ambiguous[conversation_id] = pending

    def get_favourite(self, conversation_id: str | None) -> PendingFavourite | None:
        if not conversation_id:
            return None
        self._purge_expired()
        return self._fav.get(conversation_id)

    def get_ambiguous(
        self, conversation_id: str | None
    ) -> PendingFavouriteDisambiguation | None:
        if not conversation_id:
            return None
        self._purge_expired()
        return self._ambiguous.get(conversation_id)

    def has_favourite(self, conversation_id: str | None) -> bool:
        return self.get_favourite(conversation_id) is not None

    def has_ambiguous(self, conversation_id: str | None) -> bool:
        return self.get_ambiguous(conversation_id) is not None

    def is_confirmable(self, conversation_id: str | None) -> bool:
        item = self.get_favourite(conversation_id)
        return bool(item and item.confirmable)

    def is_dutch(self, conversation_id: str | None) -> bool:
        fav = self.get_favourite(conversation_id)
        if fav is not None:
            return fav.dutch
        amb = self.get_ambiguous(conversation_id)
        return bool(amb and amb.dutch)

    def clear(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        self._fav.pop(conversation_id, None)
        self._ambiguous.pop(conversation_id, None)

    def clear_favourite(
        self, conversation_id: str | None
    ) -> PendingFavourite | None:
        if not conversation_id:
            return None
        return self._fav.pop(conversation_id, None)


# --- Intent ---------------------------------------------------------------

_FAV_NEGATION = re.compile(
    r"\b("
    r"don'?t|do\s+not|never|niet|geen\s+favoriet|"
    r"don'?t\s+mark|niet\s+markeren|"
    r"not\s+mark|not\s+favourite|not\s+favorite"
    r")\b",
    re.IGNORECASE,
)
_FAV_QUESTION = re.compile(
    r"^\s*(what|which|wie|wat|welke|how's|how\s+is|is\s+|should\s+i)\b|"
    r"\b(what('?s|\s+is)\s+my\s+favou?rite|wat\s+is\s+mijn\s+favoriet)|"
    r"\?$",
    re.IGNORECASE,
)
_FAV_GENRE_PREF = re.compile(
    r"\bfavou?rite\s+genres?\b|"
    r"\bfavoriete?\s+genres?\b|"
    r"\bmy\s+favou?rite\s+genres?\b",
    re.IGNORECASE,
)
_FAV_EPISODE = re.compile(
    r"\b(episode|aflevering|season|seizoen)\b",
    re.IGNORECASE,
)
_FAV_BULK = re.compile(
    r"\b(all|every|alle|elke)\b.*\bfavou?rite|"
    r"\bfavou?rite\b.*\b(all|every|alle|elke|genre|genres|movies|films|shows|series)\b",
    re.IGNORECASE,
)

_MARK_PATTERNS = [
    re.compile(r"\b(?:mark|set|add)\s+(.+?)\s+as\s+a?\s*favou?rite\b", re.I),
    re.compile(r"\b(?:make)\s+(.+?)\s+(?:a\s+)?favou?rite\b", re.I),
    re.compile(r"\bfavou?rite\s+(.+)$", re.I),
    re.compile(r"\bmaak\s+(.+?)\s+(?:een\s+)?favoriet\b", re.I),
    re.compile(r"\bmarkeer\s+(.+?)\s+als\s+favoriet\b", re.I),
    re.compile(r"\bfavoriet\s+(.+)$", re.I),
]

_UNMARK_PATTERNS = [
    re.compile(
        r"\b(?:remove|clear)\s+(?:the\s+)?favou?rite\s+(?:from|on|for)\s+(.+)$",
        re.I,
    ),
    re.compile(r"\bunfavou?rite\s+(.+)$", re.I),
    re.compile(
        r"\b(?:remove|clear)\s+(.+?)\s+from\s+(?:my\s+)?favou?rites?\b", re.I
    ),
    re.compile(
        r"\bhaal\s+(?:de\s+)?favoriet\s+(?:van|van\s+de)\s+(.+?)\s+weg\b", re.I
    ),
    re.compile(r"\bverwijder\s+(?:de\s+)?favoriet\s+(?:van|op)\s+(.+)$", re.I),
    re.compile(r"\bunfavoriet\s+(.+)$", re.I),
]


def _clean_title(raw: str) -> str | None:
    title = (raw or "").strip(" \t\"'`.,!?")
    title = re.sub(r"\s+", " ", title).strip()
    # Drop trailing "as a favourite" leftovers if any.
    title = re.sub(
        r"(?i)\s+as\s+a?\s*favou?rite\s*$", "", title
    ).strip()
    return title or None


def favourite_locale_dutch(text: str) -> bool:
    msg = (text or "").lower()
    return any(
        w in msg
        for w in (
            "favoriet",
            "favorieten",
            "maak",
            "markeer",
            "haal",
            "verwijder",
            "weg",
        )
    )


def extract_favourite_request(text: str) -> FavouriteRequest | None:
    """Parse favourite / unfavourite + title. None when not this intent."""
    raw = (text or "").strip()
    if not raw:
        return None
    if _FAV_NEGATION.search(raw):
        return None
    if _FAV_QUESTION.search(raw):
        return None
    if _FAV_GENRE_PREF.search(raw):
        return None
    if _FAV_EPISODE.search(raw):
        return None
    if _FAV_BULK.search(raw):
        return None

    dutch = favourite_locale_dutch(raw)

    for pat in _UNMARK_PATTERNS:
        m = pat.search(raw)
        if m:
            title = _clean_title(m.group(1))
            if title:
                return FavouriteRequest(title=title, favorite=False, dutch=dutch)

    for pat in _MARK_PATTERNS:
        m = pat.search(raw)
        if m:
            title = _clean_title(m.group(1))
            if title:
                return FavouriteRequest(title=title, favorite=True, dutch=dutch)

    return None


def user_message_requests_favourite(text: str) -> bool:
    return extract_favourite_request(text) is not None


def should_keep_pending_favourite(
    text: str, *, has_ambiguous: bool = False
) -> bool:
    if not (text or "").strip():
        return False
    if is_bare_confirm(text) or is_bare_cancel(text):
        return True
    if is_repeat_intent(text):
        return True
    if user_message_requests_favourite(text):
        return True
    if has_ambiguous:
        raw = text.strip()
        if re.fullmatch(r"\d{1,2}", raw):
            return True
        if re.fullmatch(r"(?:19|20)\d{2}", raw):
            return True
        if len(raw) <= 80 and not raw.endswith("?"):
            return True
    return False


# --- Resolve --------------------------------------------------------------


def _candidate_from_raw(raw: dict[str, Any]) -> FavouriteCandidate | None:
    kind_raw = str(raw.get("Type") or "").strip()
    if kind_raw == "Movie":
        kind = "movie"
    elif kind_raw == "Series":
        kind = "series"
    else:
        return None
    item_id = raw.get("Id")
    name = raw.get("Name")
    if not item_id or not name:
        return None
    year = raw.get("ProductionYear")
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            year = None
    return FavouriteCandidate(
        item_id=str(item_id),
        kind=kind,
        title=str(name),
        year=year,
    )


def resolve_favourite_target(
    client: JellyfinClient,
    title: str,
) -> ResolveResult:
    """Live resolve via ``find_media``. Fail closed on truncation."""
    needle = (title or "").strip()
    if not needle:
        return ResolveResult(status="missing")
    try:
        raws, truncated = client.find_media(title=needle, include_series=True)
    except JellyfinError as exc:
        return ResolveResult(status="error", error=str(exc))

    if truncated:
        return ResolveResult(status="truncated")

    candidates = [
        c for raw in raws if (c := _candidate_from_raw(raw)) is not None
    ]
    if not candidates:
        return ResolveResult(status="missing")

    lower = needle.casefold()
    exact = [c for c in candidates if c.title.casefold() == lower]
    pool = exact if exact else candidates

    # Exact film + exact series → ask (never movie-wins).
    if exact:
        kinds = {c.kind for c in exact}
        if len(kinds) > 1 or len(exact) > 1:
            # Deduplicate by item_id
            seen: set[str] = set()
            uniq: list[FavouriteCandidate] = []
            for c in exact:
                if c.item_id in seen:
                    continue
                seen.add(c.item_id)
                uniq.append(c)
            if len(uniq) > 1:
                return ResolveResult(status="ambiguous", candidates=uniq)
            return ResolveResult(status="ok", candidates=uniq)

    if len(pool) == 1:
        return ResolveResult(status="ok", candidates=pool)

    # Multiple substring (or multiple exact same kind already handled)
    seen2: set[str] = set()
    uniq2: list[FavouriteCandidate] = []
    for c in pool:
        if c.item_id in seen2:
            continue
        seen2.add(c.item_id)
        uniq2.append(c)
    if len(uniq2) == 1:
        return ResolveResult(status="ok", candidates=uniq2)
    return ResolveResult(status="ambiguous", candidates=uniq2)


def resolve_favourite_disambiguation_pick(
    text: str, amb: PendingFavouriteDisambiguation
) -> FavouriteCandidate | None:
    raw = (text or "").strip()
    if not raw or not amb.candidates:
        return None
    if re.fullmatch(r"\d{1,2}", raw):
        idx = int(raw) - 1
        if 0 <= idx < len(amb.candidates):
            return amb.candidates[idx]
        return None
    year_m = re.fullmatch(r"((?:19|20)\d{2})", raw)
    if year_m:
        year = int(year_m.group(1))
        hits = [c for c in amb.candidates if c.year == year]
        return hits[0] if len(hits) == 1 else None
    lower = raw.casefold()
    # Exact title or "Title (year)"
    for c in amb.candidates:
        label = c.title.casefold()
        with_year = f"{c.title} ({c.year})".casefold() if c.year is not None else ""
        if lower == label or (with_year and lower == with_year):
            return c
        if c.kind.casefold() == lower and sum(
            1 for x in amb.candidates if x.kind == c.kind
        ) == 1:
            return c
    # Kind + title
    for c in amb.candidates:
        if lower == f"{c.kind} {c.title}".casefold():
            return c
        if lower == f"{c.title} {c.kind}".casefold():
            return c
    return None


# --- Replies --------------------------------------------------------------


def _label(c: FavouriteCandidate | PendingFavourite) -> str:
    title = c.title
    year = c.year
    if year is not None:
        return f"{title} ({year})"
    return title


def build_favourite_confirm_reply(pending: PendingFavourite) -> str:
    label = _label(pending)
    kind = "film" if pending.kind == "movie" else (
        "serie" if pending.dutch else "series"
    )
    if pending.favorite:
        if pending.dutch:
            return (
                f"**{label}** ({kind}) als favoriet markeren — nog niet gedaan.\n"
                "Zeg *ja* of tik Confirm."
            )
        return (
            f"Mark **{label}** ({kind}) as a favourite — not done yet.\n"
            "Say *yes* or tap Confirm."
        )
    if pending.dutch:
        return (
            f"Favoriet van **{label}** ({kind}) verwijderen — nog niet gedaan.\n"
            "Zeg *ja* of tik Confirm."
        )
    return (
        f"Remove favourite from **{label}** ({kind}) — not done yet.\n"
        "Say *yes* or tap Confirm."
    )


def build_favourite_success_reply(
    pending: PendingFavourite, *, is_favorite: bool
) -> str:
    label = _label(pending)
    if pending.dutch:
        if is_favorite:
            return f"Klaar — **{label}** is nu een favoriet."
        return f"Klaar — **{label}** is geen favoriet meer."
    if is_favorite:
        return f"Done — **{label}** is now a favourite."
    return f"Done — **{label}** is not a favourite."


def build_favourite_ambiguous_reply(
    query: str, candidates: list[FavouriteCandidate], *, dutch: bool
) -> str:
    lines: list[str] = []
    for i, c in enumerate(candidates, start=1):
        kind = "film" if c.kind == "movie" else ("serie" if dutch else "series")
        lines.append(f"{i}. {_label(c)} ({kind})")
    body = "\n".join(lines)
    if dutch:
        return (
            f"Meerdere treffers voor **{query}**. Welke?\n{body}\n"
            "Zeg het nummer, de titel, of film/serie."
        )
    return (
        f"Several matches for **{query}**. Which one?\n{body}\n"
        "Say the number, the title, or movie/series."
    )


def build_favourite_failure_reply(message: str, *, dutch: bool) -> str:
    return message


def build_favourite_missing_reply(title: str, *, dutch: bool) -> str:
    if dutch:
        return f"Ik vind **{title}** niet in Jellyfin."
    return f"I can't find **{title}** in Jellyfin."


def build_favourite_truncated_reply(*, dutch: bool) -> str:
    if dutch:
        return (
            "Te veel treffers om veilig te kiezen — noem de exacte titel "
            "(of film vs serie)."
        )
    return (
        "Too many matches to choose safely — give the exact title "
        "(or movie vs series)."
    )


def build_favourite_cancel_reply(*, dutch: bool) -> str:
    if dutch:
        return "Oké — favoriet niet gewijzigd."
    return "Okay — favourite not changed."


# --- Execute --------------------------------------------------------------


def jellyfin_ready_for_favourite(settings: Settings) -> bool:
    return _jellyfin_ready(settings)


def client_from_settings(settings: Settings) -> JellyfinClient | None:
    return _client_from_settings(settings)


def execute_favourite_write(
    settings: Settings,
    pending: PendingFavourite,
    *,
    client: JellyfinClient | None = None,
) -> tuple[bool, str]:
    """Returns (ok, reply). Performs the Jellyfin write."""
    owns = client is None
    jf = client if client is not None else client_from_settings(settings)
    if jf is None:
        msg = (
            "Jellyfin is niet geconfigureerd."
            if pending.dutch
            else "Jellyfin is not configured."
        )
        return False, msg
    try:
        flag = jf.set_favorite(pending.item_id, favorite=pending.favorite)
    except JellyfinError as exc:
        if pending.dutch:
            return False, f"Favoriet wijzigen mislukt: {exc}"
        return False, f"Could not update favourite: {exc}"
    finally:
        if owns and jf is not None:
            jf.close()

    if flag != pending.favorite:
        if pending.dutch:
            return False, "Favorietstatus kon niet worden bevestigd."
        return False, "Favourite state could not be verified."
    return True, build_favourite_success_reply(pending, is_favorite=flag)


def stage_favourite_from_request(
    settings: Settings,
    req: FavouriteRequest,
    *,
    client: JellyfinClient | None = None,
) -> tuple[str, PendingFavourite | None, PendingFavouriteDisambiguation | None]:
    """Resolve + decide stage. Returns (reply, pending|None, ambiguous|None).

    Never writes.
    """
    owns = client is None
    jf = client if client is not None else client_from_settings(settings)
    if jf is None:
        msg = (
            "Jellyfin is niet geconfigureerd."
            if req.dutch
            else "Jellyfin is not configured."
        )
        return msg, None, None
    try:
        resolved = resolve_favourite_target(jf, req.title)
    finally:
        if owns and jf is not None:
            jf.close()

    if resolved.status == "error":
        err = resolved.error or "jellyfin unavailable"
        if req.dutch:
            return f"Favoriet wijzigen mislukt: {err}", None, None
        return f"Could not update favourite: {err}", None, None
    if resolved.status == "truncated":
        return build_favourite_truncated_reply(dutch=req.dutch), None, None
    if resolved.status == "missing" or not resolved.candidates:
        return build_favourite_missing_reply(req.title, dutch=req.dutch), None, None
    if resolved.status == "ambiguous":
        amb = PendingFavouriteDisambiguation(
            query=req.title,
            favorite=req.favorite,
            candidates=list(resolved.candidates),
            dutch=req.dutch,
        )
        return (
            build_favourite_ambiguous_reply(
                req.title, amb.candidates, dutch=req.dutch
            ),
            None,
            amb,
        )

    c = resolved.candidates[0]
    pending = PendingFavourite(
        item_id=c.item_id,
        title=c.title,
        year=c.year,
        kind=c.kind,
        favorite=req.favorite,
        dutch=req.dutch,
    )
    return build_favourite_confirm_reply(pending), pending, None
