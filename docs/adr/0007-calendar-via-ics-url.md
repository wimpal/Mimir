# Calendar via provider-agnostic ICS URL

We need read-only schedule for morning briefs and “what’s on today.” Proton has no native CalDAV; a local bridge is optional later. We decided the brain reads configured **Calendar feed(s)** (ICS subscribe URL(s))—provider-agnostic so switching calendars is a URL change, not a new integration. Multiple named feeds use `calendar.feeds` (id + display name) with secrets `CALENDAR_ICS_URL_<ID>`; a legacy single `CALENDAR_ICS_URL` applies only when `feeds` is empty. Fetch on demand with weather-like TTL failover; document publisher lag (e.g. Proton share links up to ~8h). CalDAV/bridge only if that lag becomes unacceptable.

**Considered options:** Proton-specific API; local CalDAV bridge first; one-shot `.ics` file import (no refresh).
