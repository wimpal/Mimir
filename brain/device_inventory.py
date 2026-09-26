"""Network device inventory staging / confirm gate (T-108)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from brain.recipe_import import is_bare_cancel, is_bare_confirm
from brain.repeat_last import is_repeat_intent

DEVICE_ADD_TOOL = "homebase.devices.add"
DEVICE_UPDATE_TOOL = "homebase.devices.update"
DEVICE_REMOVE_TOOL = "homebase.devices.remove"
DEVICE_WAKE_TOOL = "homebase.devices.wake"
DEVICE_GO_HOME_TOOL = "homebase.devices.go_home"
DEVICE_LAUNCH_APP_TOOL = "homebase.devices.launch_app"
DEVICE_SET_INPUT_TOOL = "homebase.devices.set_input"
DEVICE_POWER_OFF_TOOL = "homebase.devices.power_off"
DEVICE_TV_TOOLS = frozenset(
    {
        DEVICE_GO_HOME_TOOL,
        DEVICE_LAUNCH_APP_TOOL,
        DEVICE_SET_INPUT_TOOL,
        DEVICE_POWER_OFF_TOOL,
    }
)
DEVICE_WRITE_TOOLS = frozenset(
    {
        DEVICE_ADD_TOOL,
        DEVICE_UPDATE_TOOL,
        DEVICE_REMOVE_TOOL,
        DEVICE_WAKE_TOOL,
        *DEVICE_TV_TOOLS,
    }
)

PENDING_TTL_S = 600.0

_DEVICE_ADD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\benroll\b",
        r"\badd\b.*\b(nas|laptop|router|phone|pc|printer|tablet|device|ap)\b",
        r"\b(nas|laptop|router|phone|pc|printer|tablet|device)\b.*\badd\b",
        r"\bvoeg\b.*\b(nas|laptop|router|telefoon|pc|printer|tablet|apparaat)\b.*\btoe\b",
        r"\bregistreer\b",
        r"\badd\b.*\b(in|to)\b.*\b(office|kantoor|woonkamer|living)\b",
    )
)

_DEVICE_UPDATE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bmove\b.*\b(nas|laptop|router|phone|pc|printer|device|apparaat)\b",
        r"\brename\b.*\b(nas|laptop|router|phone|pc|printer|device|apparaat)\b",
        r"\bupdate\b.*\b(nas|laptop|router|phone|pc|printer|device|apparaat)\b",
        r"\bverplaats\b",
        r"\bhernoem\b.*\b(nas|laptop|router|telefoon|pc|printer|apparaat)\b",
        r"\bchange\b.*\b(location|locatie)\b",
    )
)

_DEVICE_REMOVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bretire\b",
        r"\bremove\b.*\b(nas|laptop|router|phone|pc|printer|device|apparaat|old)\b",
        r"\bverwijder\b.*\b(nas|laptop|router|telefoon|pc|printer|apparaat|oude)\b",
        r"\buitschakel\b.*\b(apparaat|device)\b",
    )
)

_DEVICE_WAKE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwake\b",
        r"\bwek\b",
        r"\bwake[- ]?on[- ]?lan\b",
        r"\bturn on\b.*\b(nas|pc|laptop|router|tv)\b",
        r"\b(nas|pc|laptop|router|tv)\b.*\bturn on\b",
        # "turn the TV/NAS on" (object between turn and on)
        r"\bturn\b.*\b(nas|pc|laptop|router|tv)\b.*\bon\b",
        r"\bzet\b.*\b(nas|pc|laptop|tv)\b.*\baan\b",
        r"\b(nas|pc|laptop|tv)\b.*\baanzetten\b",
    )
)

_DEVICE_GO_HOME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(go to|open|switch to)\b.*\bhome\b.*\b(tv|television)\b",
        r"\b(tv|television)\b.*\b(go to|open)\b.*\bhome\b",
        # "switch the tv to home" / "switch tv to home" (home after tv)
        r"\bswitch\b.*\b(tv|television)\b.*\bto\b.*\bhome\b",
        r"\b(tv|television)\b.*\bto\b.*\bhome\b",
        r"\bga naar home\b.*\b(tv|televisie)?\b",
        r"\btv\b.*\bnaar home\b",
        r"\bzet\b.*\b(tv|televisie)\b.*\b(op|naar)\b.*\bhome\b",
        # wake … home (explicit)
        r"\bwake\b.*\bhome\b",
        r"\bwek\b.*\bhome\b",
        r"\bwake\b.*\b(tv|television)\b.*\bhome\b",
        r"\b(tv|television)\b.*\bwake\b.*\bhome\b",
    )
)

# Bare "wake the TV" → Home (household default). Not NAS/PC; not HDMI/console.
_DEVICE_TV_WAKE_HOME_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwake\b.*\b(tv|television)\b",
        r"\b(tv|television)\b.*\bwake\b",
        r"\bwek\b.*\b(tv|televisie)\b",
        r"\b(tv|televisie)\b.*\bwek\b",
        r"\bturn on\b.*\b(tv|television)\b",
        r"\b(tv|television)\b.*\bturn on\b",
        # "turn the TV on" (object between turn and on)
        r"\bturn\b.*\b(tv|television)\b.*\bon\b",
        r"\bzet\b.*\b(tv|televisie)\b.*\baan\b",
        r"\b(tv|televisie)\b.*\baanzetten\b",
    )
)

# T-114: start/run/open/launch (+ NL) imply cold-start wake; switch-to does not.
_DEVICE_JELLYFIN_LAUNCH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(start|open|launch|run)\b.*\bjellyfin\b",
        r"\bstart jellyfin\b",
        r"\bzet\b.*\bjellyfin\b.*\baan\b",
        r"\bjellyfin\b.*\baanzetten\b",
    )
)

_DEVICE_JELLYFIN_SWITCH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(switch\s+to|switch\s+over\s+to)\b.*\bjellyfin\b",
        r"\bschakel\b.*\b(naar\s+)?jellyfin\b",
        r"\b(tv|television|televisie)\b.*\b(naar|to)\b.*\bjellyfin\b",
    )
)

_DEVICE_JELLYFIN_TV_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bjellyfin\b.*\b(tv|television|televisie)\b",
        r"\bjellyfin\b.*\bop de\b.*(tv|televisie)\b",
    )
)

_DEVICE_JELLYFIN_PATTERNS: tuple[re.Pattern[str], ...] = (
    *_DEVICE_JELLYFIN_LAUNCH_PATTERNS,
    *_DEVICE_JELLYFIN_SWITCH_PATTERNS,
    *_DEVICE_JELLYFIN_TV_PATTERNS,
)

TV_WAKE_RECENT_WINDOW_S = 60.0

_DEVICE_HDMI_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bhdmi\s*1\b",
        r"\bswitch\b.*\b(tv|television)\b.*\bhdmi\b",
        r"\btv\b.*\bhdmi\b",
        r"\bplaystation\b.*\b(tv|television)?\b",
        r"\b(console|game)\b.*\b(tv|hdmi)\b",
    )
)

# TV-worded power-off only (T-113). Not bare lights-off / good-night.
_DEVICE_POWER_OFF_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bturn\s+off\b.*\b(tv|television)\b",
        r"\b(tv|television)\b.*\bturn\s+off\b",
        # "turn the TV off" / "turn tv off" (object between turn and off)
        r"\bturn\b.*\b(tv|television)\b.*\boff\b",
        r"\bswitch\s+off\b.*\b(tv|television)\b",
        r"\b(tv|television)\b.*\bswitch\s+off\b",
        r"\bswitch\b.*\b(tv|television)\b.*\boff\b",
        r"\bpower\s+off\b.*\b(tv|television)\b",
        r"\b(tv|television)\b.*\bpower\s+off\b",
        r"\b(tv|televisie)\b\s+uit\b",
        r"\bzet\b.*\b(tv|televisie)\b.*\buit\b",
        r"\b(tv|televisie)\b.*\buitzetten\b",
        r"\bschakel\b.*\b(tv|televisie)\b.*\buit\b",
    )
)

_DEVICE_POWER_OFF_STATUS = re.compile(
    r"\b(is|staat|stand)\b.*\b(tv|television|televisie)\b.*\b(off|uit|aan)\b|"
    r"\b(tv|television|televisie)\b.*\b(is|staat)\b.*\b(off|uit|aan)\b",
    re.IGNORECASE,
)

_DEVICE_WRITE_NEGATION = re.compile(
    r"\b(don'?t|do\s+not|niet|geen)\b.*\b(add|enroll|remove|retire|move|wake|wek|"
    r"voeg|verwijder|verplaats|registreer|turn\s+off|turn\s+on|uitzetten|uit|"
    r"switch|jellyfin|run|launch|start|open|schakel)\b|"
    r"\b(don'?t|do\s+not|niet|geen)\b.*\bturn\b.*\b(off|on)\b",
    re.IGNORECASE,
)

_LIGHTS_CARVEOUT = re.compile(
    r"\b(light|lights|lamp|lampen|licht|lichten)\b",
    re.IGNORECASE,
)


@dataclass
class PendingDevice:
    payload: dict[str, Any]
    created_monotonic: float = field(default_factory=time.monotonic)
    confirmable: bool = True
    dutch: bool = False
    tool_name: str = DEVICE_ADD_TOOL


class PendingDeviceStore:
    """In-memory pending Network device writes keyed by conversation id."""

    def __init__(self, *, ttl_s: float = PENDING_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._by_conversation: dict[str, PendingDevice] = {}
        # Last successful TV tool with wake_if_needed (T-114 rate-limit suppress).
        # Keyed by device_id when known (matches Homebase per-device WoL window),
        # else by conversation id.
        self._tv_wake_at: dict[str, float] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, item in self._by_conversation.items()
            if now - item.created_monotonic > self._ttl_s
        ]
        for key in expired:
            del self._by_conversation[key]
        wake_expired = [
            key
            for key, at in self._tv_wake_at.items()
            if now - at > TV_WAKE_RECENT_WINDOW_S * 2
        ]
        for key in wake_expired:
            del self._tv_wake_at[key]

    @staticmethod
    def _tv_wake_key(
        conversation_id: str | None, *, device_id: str | None = None
    ) -> str | None:
        did = (device_id or "").strip()
        if did:
            return f"d:{did}"
        if conversation_id:
            return f"c:{conversation_id}"
        return None

    def record_tv_wake(
        self,
        conversation_id: str | None,
        *,
        device_id: str | None = None,
    ) -> None:
        """Mark a successful wake_if_needed TV dispatch (not dry_run)."""
        key = self._tv_wake_key(conversation_id, device_id=device_id)
        if not key:
            return
        self._tv_wake_at[key] = time.monotonic()

    def tv_wake_recent(
        self,
        conversation_id: str | None,
        *,
        device_id: str | None = None,
        window_s: float = TV_WAKE_RECENT_WINDOW_S,
    ) -> bool:
        """True if this device/conversation woke with wake_if_needed within window_s."""
        now = time.monotonic()
        keys: list[str] = []
        did = (device_id or "").strip()
        if did:
            keys.append(f"d:{did}")
        if conversation_id:
            keys.append(f"c:{conversation_id}")
        for key in keys:
            at = self._tv_wake_at.get(key)
            if at is not None and (now - at) <= window_s:
                return True
        return False

    def set(
        self,
        conversation_id: str,
        payload: dict[str, Any],
        *,
        dutch: bool | None = None,
        tool_name: str = DEVICE_ADD_TOOL,
    ) -> None:
        self._purge_expired()
        prior = self._by_conversation.get(conversation_id)
        if dutch is not None:
            locale = dutch
        elif prior is not None:
            locale = prior.dutch
        else:
            locale = False
        self._by_conversation[conversation_id] = PendingDevice(
            payload=dict(payload),
            confirmable=True,
            dutch=locale,
            tool_name=tool_name if tool_name in DEVICE_WRITE_TOOLS else DEVICE_ADD_TOOL,
        )

    def get(self, conversation_id: str | None) -> dict[str, Any] | None:
        if not conversation_id:
            return None
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        return dict(item.payload) if item is not None else None

    def get_tool_name(self, conversation_id: str | None) -> str:
        if not conversation_id:
            return DEVICE_ADD_TOOL
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        if item is None:
            return DEVICE_ADD_TOOL
        return item.tool_name or DEVICE_ADD_TOOL

    def is_dutch(self, conversation_id: str | None) -> bool:
        if not conversation_id:
            return False
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        return bool(item is not None and item.dutch)

    def is_confirmable(self, conversation_id: str | None) -> bool:
        if not conversation_id:
            return False
        self._purge_expired()
        item = self._by_conversation.get(conversation_id)
        return bool(item is not None and item.confirmable)

    def clear(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        self._by_conversation.pop(conversation_id, None)

    def has(self, conversation_id: str | None) -> bool:
        return self.get(conversation_id) is not None


def _device_write_negated(text: str) -> bool:
    return bool((text or "").strip()) and _DEVICE_WRITE_NEGATION.search(text) is not None


def _lights_carveout(text: str) -> bool:
    return bool((text or "").strip()) and _LIGHTS_CARVEOUT.search(text) is not None


def user_message_requests_device_add(text: str) -> bool:
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    return any(p.search(text) for p in _DEVICE_ADD_PATTERNS)


def user_message_requests_device_update(text: str) -> bool:
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    return any(p.search(text) for p in _DEVICE_UPDATE_PATTERNS)


def user_message_requests_device_remove(text: str) -> bool:
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    return any(p.search(text) for p in _DEVICE_REMOVE_PATTERNS)


def user_message_requests_device_wake(text: str) -> bool:
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    return any(p.search(text) for p in _DEVICE_WAKE_PATTERNS)


def user_message_requests_device_write(text: str) -> bool:
    return (
        user_message_requests_device_add(text)
        or user_message_requests_device_update(text)
        or user_message_requests_device_tv(text)
        or user_message_requests_device_remove(text)
        or user_message_requests_device_wake(text)
    )


def may_stage_device_add(text: str, *, has_pending: bool) -> bool:
    if is_bare_confirm(text) or is_bare_cancel(text):
        return False
    if user_message_requests_device_add(text):
        return True
    return has_pending and user_message_requests_device_write(text)


def may_stage_device_update(text: str, *, has_pending: bool) -> bool:
    if is_bare_confirm(text) or is_bare_cancel(text):
        return False
    if user_message_requests_device_update(text):
        return True
    return has_pending and user_message_requests_device_write(text)


def may_stage_device_remove(text: str, *, has_pending: bool) -> bool:
    if is_bare_confirm(text) or is_bare_cancel(text):
        return False
    if user_message_requests_device_remove(text):
        return True
    return has_pending and user_message_requests_device_write(text)


def may_stage_device_wake(text: str, *, has_pending: bool) -> bool:
    if is_bare_confirm(text) or is_bare_cancel(text):
        return False
    if user_message_requests_device_wake(text):
        return True
    return has_pending and user_message_requests_device_write(text)


def user_message_requests_device_tv_wake_home(text: str) -> bool:
    """Wake/turn-on the TV with no HDMI/console/Jellyfin ask → go_home path."""
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    if user_message_requests_device_hdmi(text) or user_message_requests_device_jellyfin(
        text
    ):
        return False
    return any(p.search(text) for p in _DEVICE_TV_WAKE_HOME_PATTERNS)


def user_message_requests_device_go_home(text: str) -> bool:
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    if any(p.search(text) for p in _DEVICE_GO_HOME_PATTERNS):
        return True
    return user_message_requests_device_tv_wake_home(text)


def user_message_requests_device_jellyfin(text: str) -> bool:
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    return any(p.search(text) for p in _DEVICE_JELLYFIN_PATTERNS)


def user_message_requests_device_jellyfin_switch(text: str) -> bool:
    """Switch-to Jellyfin (TV already on) — no wake_if_needed."""
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    if not any(p.search(text) for p in _DEVICE_JELLYFIN_SWITCH_PATTERNS):
        return False
    # Launch/wake verbs in the same message still count as cold-start, not switch-only.
    if any(p.search(text) for p in _DEVICE_JELLYFIN_LAUNCH_PATTERNS):
        return False
    if user_message_requests_device_wake(text):
        return False
    return True


def jellyfin_wants_wake(text: str) -> bool:
    """Whether jellyfin remap should set wake_if_needed (before recent-wake suppress)."""
    if not user_message_requests_device_jellyfin(text):
        return False
    if user_message_requests_device_jellyfin_switch(text):
        return False
    if user_message_requests_device_wake(text):
        return True
    return any(p.search(text) for p in _DEVICE_JELLYFIN_LAUNCH_PATTERNS)


def user_message_requests_device_hdmi(text: str) -> bool:
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    return any(p.search(text) for p in _DEVICE_HDMI_PATTERNS)


def user_message_requests_device_power_off(text: str) -> bool:
    if not (text or "").strip():
        return False
    if _device_write_negated(text) or _lights_carveout(text):
        return False
    if _DEVICE_POWER_OFF_STATUS.search(text):
        return False
    return any(p.search(text) for p in _DEVICE_POWER_OFF_PATTERNS)


def user_message_requests_device_tv(text: str) -> bool:
    return (
        user_message_requests_device_go_home(text)
        or user_message_requests_device_jellyfin(text)
        or user_message_requests_device_hdmi(text)
        or user_message_requests_device_power_off(text)
    )


def may_stage_device_tv(text: str, *, has_pending: bool) -> bool:
    if is_bare_confirm(text) or is_bare_cancel(text):
        return False
    if user_message_requests_device_tv(text):
        return True
    return has_pending and user_message_requests_device_write(text)


def should_keep_pending_device(text: str) -> bool:
    if not (text or "").strip():
        return False
    if is_bare_confirm(text) or is_bare_cancel(text):
        return True
    if is_repeat_intent(text):
        return True
    return user_message_requests_device_write(text)


def device_locale_dutch(user_message: str, *, prefer_dutch: bool | None = None) -> bool:
    if prefer_dutch is not None:
        return prefer_dutch
    msg = (user_message or "").lower()
    nl_hits = sum(
        1
        for w in (
            "voeg",
            "verplaats",
            "verwijder",
            "wek",
            "waar",
            "kantoor",
            "woonkamer",
            "apparaat",
            "uit",
            "televisie",
            "uitzetten",
            "schakel",
        )
        if w in msg
    )
    return nl_hits >= 1


def awaiting_device_confirmation_result(payload: dict[str, Any], tool_name: str) -> str:
    body = {
        "status": "awaiting_confirmation",
        "tool": tool_name,
        "name": payload.get("name"),
        "type": payload.get("type"),
        "location": payload.get("location"),
        "id": payload.get("id") or payload.get("device_id"),
        "device_id": payload.get("device_id") or payload.get("id"),
        "saved": False,
        "note": "Not written yet — ask the user to confirm before claiming success.",
    }
    return json.dumps(body, ensure_ascii=False)


def enrich_device_stage_name(
    payload: dict[str, Any],
    *,
    list_json_candidates: list[str],
) -> dict[str, Any]:
    """Attach display name from a prior devices.list result when staging wake/update."""
    out = dict(payload)
    if str(out.get("name") or "").strip():
        return out
    needle = str(out.get("device_id") or out.get("id") or "").strip()
    if not needle:
        return out
    from brain.mcp.devices import parse_devices_list

    for raw in list_json_candidates:
        devices = parse_devices_list(raw)
        if not devices:
            continue
        for d in devices:
            if str(d.get("id") or "") == needle:
                name = str(d.get("name") or "").strip()
                if name:
                    out["name"] = name
                return out
    return out


def device_dispatch_args(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Args actually sent to MCP — drop display-only fields kept for confirm copy."""
    if tool_name == DEVICE_WAKE_TOOL or tool_name == DEVICE_POWER_OFF_TOOL:
        device_id = str(payload.get("device_id") or payload.get("id") or "").strip()
        return {"device_id": device_id} if device_id else {}
    if tool_name in DEVICE_TV_TOOLS:
        out: dict[str, Any] = {}
        device_id = str(payload.get("device_id") or payload.get("id") or "").strip()
        if device_id:
            out["device_id"] = device_id
        if tool_name == DEVICE_LAUNCH_APP_TOOL:
            target = str(payload.get("target") or "").strip()
            if target:
                out["target"] = target
        if tool_name == DEVICE_SET_INPUT_TOOL:
            inp = str(payload.get("input") or "").strip()
            if inp:
                out["input"] = inp
        if payload.get("wake_if_needed") is True:
            out["wake_if_needed"] = True
        return out
    return dict(payload)


def build_device_confirm_reply(
    payload: dict[str, Any],
    tool_name: str,
    *,
    user_message: str = "",
    dutch: bool | None = None,
) -> str:
    use_dutch = device_locale_dutch(user_message, prefer_dutch=dutch)
    name = str(
        payload.get("name")
        or payload.get("id")
        or payload.get("device_id")
        or "device"
    )
    typ = str(payload.get("type") or "").strip()
    loc = str(payload.get("location") or "").strip()
    detail_bits = [b for b in (typ, loc) if b]
    detail = f" ({', '.join(detail_bits)})" if detail_bits else ""

    if tool_name == DEVICE_WAKE_TOOL:
        if use_dutch:
            return (
                f"**{name}** wakker maken (Wake-on-LAN) — nog niet gedaan.\n"
                "Zeg *ja* of tik Confirm."
            )
        return (
            f"Wake **{name}** (Wake-on-LAN) — not done yet.\n"
            "Say *yes* or tap Confirm."
        )
    if tool_name == DEVICE_GO_HOME_TOOL:
        if use_dutch:
            return (
                f"**{name}** naar Home (webOS) — nog niet gedaan.\n"
                "Zeg *ja* of tik Confirm."
            )
        return (
            f"Open Home on **{name}** (webOS) — not done yet.\n"
            "Say *yes* or tap Confirm."
        )
    if tool_name == DEVICE_LAUNCH_APP_TOOL:
        target = str(payload.get("target") or "app")
        if use_dutch:
            return (
                f"**{target}** starten op **{name}** — nog niet gedaan.\n"
                "Zeg *ja* of tik Confirm."
            )
        return (
            f"Launch **{target}** on **{name}** — not done yet.\n"
            "Say *yes* or tap Confirm."
        )
    if tool_name == DEVICE_SET_INPUT_TOOL:
        inp = str(payload.get("input") or "input")
        if use_dutch:
            return (
                f"**{name}** naar **{inp}** — nog niet gedaan.\n"
                "Zeg *ja* of tik Confirm."
            )
        return (
            f"Switch **{name}** to **{inp}** — not done yet.\n"
            "Say *yes* or tap Confirm."
        )
    if tool_name == DEVICE_POWER_OFF_TOOL:
        if use_dutch:
            return (
                f"**{name}** uitzetten (webOS) — nog niet gedaan.\n"
                "Zeg *ja* of tik Confirm."
            )
        return (
            f"Turn off **{name}** (webOS) — not done yet.\n"
            "Say *yes* or tap Confirm."
        )
    if tool_name == DEVICE_REMOVE_TOOL:
        if use_dutch:
            return (
                f"Apparaat **{name}** uitschakelen (soft-retire) — nog niet gedaan.\n"
                "Zeg *ja* of tik Confirm."
            )
        return (
            f"Retire **{name}** (soft-retire) — not done yet.\n"
            "Say *yes* or tap Confirm."
        )
    if tool_name == DEVICE_UPDATE_TOOL:
        if use_dutch:
            return (
                f"Apparaat **{name}** bijwerken{detail} — nog niet opgeslagen.\n"
                "Zeg *ja* of tik Confirm."
            )
        return (
            f"Update **{name}**{detail} — not saved yet.\n"
            "Say *yes* or tap Confirm."
        )
    if use_dutch:
        return (
            f"Apparaat **{name}** toevoegen{detail} — nog niet opgeslagen.\n"
            "Zeg *ja* of tik Confirm."
        )
    return (
        f"Enroll **{name}**{detail} — not saved yet.\n"
        "Say *yes* or tap Confirm."
    )


def build_device_success_reply(
    payload: dict[str, Any],
    tool_name: str,
    *,
    dutch: bool = False,
) -> str:
    name = str(payload.get("name") or "device")
    if tool_name == DEVICE_WAKE_TOOL:
        status = str(payload.get("status") or "")
        if status == "dry_run":
            return (
                f"**{name}** wake dry-run (geen packet verstuurd)."
                if dutch
                else f"**{name}** wake dry-run (packet not sent)."
            )
        return (
            f"**{name}** is gewekt."
            if dutch
            else f"**{name}** wake packet sent."
        )
    if tool_name == DEVICE_GO_HOME_TOOL:
        return (
            f"**{name}** staat op Home."
            if dutch
            else f"**{name}** switched to Home."
        )
    if tool_name == DEVICE_LAUNCH_APP_TOOL:
        target = str(payload.get("target") or "app")
        return (
            f"**{target}** is gestart op **{name}**."
            if dutch
            else f"**{target}** launched on **{name}**."
        )
    if tool_name == DEVICE_SET_INPUT_TOOL:
        inp = str(payload.get("input") or "input")
        return (
            f"**{name}** staat op **{inp}**."
            if dutch
            else f"**{name}** switched to **{inp}**."
        )
    if tool_name == DEVICE_POWER_OFF_TOOL:
        return (
            f"**{name}** is uitgezet."
            if dutch
            else f"**{name}** turned off."
        )
    if tool_name == DEVICE_REMOVE_TOOL:
        return (
            f"**{name}** is uitgeschakeld."
            if dutch
            else f"**{name}** has been retired."
        )
    if tool_name == DEVICE_UPDATE_TOOL:
        return (
            f"**{name}** is bijgewerkt."
            if dutch
            else f"**{name}** has been updated."
        )
    return (
        f"**{name}** is toegevoegd aan Home network."
        if dutch
        else f"**{name}** has been enrolled in Home network."
    )
