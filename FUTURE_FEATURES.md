# Mimir — Future features (durable backlog)

Ideas beyond the current implementation phases. This file is a **durable backlog**: items stay here after they are promoted into [`ROADMAP.md`](./ROADMAP.md) or finished. Status changes; entries are not deleted.

**Supersedes** the earlier workflow (“add to roadmap, then remove from this file”). Promotion now means: copy **tools + context** into the ROADMAP phase notes, set status to `in-roadmap`, and keep the entry.

**Heim** (household mesh) owns cross-project order and hardware gates: [`../ProjectOverview/HEIM.md`](../ProjectOverview/HEIM.md). Use that before promoting sibling-dependent items. Sibling map: [`CONNECTIONS.md` §5](../ProjectOverview/CONNECTIONS.md) / [§8 Heim](../ProjectOverview/CONNECTIONS.md). Project pages: [Homebase](../ProjectOverview/projects/Homebase.md), [BudgetTracker](../ProjectOverview/projects/BudgetTracker.md), [Grimoire](../ProjectOverview/projects/Grimoire.md).

| Backlog cluster (this file) | Earliest Heim wave |
|-----------------------------|--------------------|
| PC-local tools, meta UX, evening wind-down (calendar + weather) | Wave 0 |
| BudgetTracker integration | Wave 1 |
| Notes → Grimoire, reading list | Wave 2 |
| Homebase shopping / todos / inventory / recipes / packages / people | Wave 3 |
| Smart home, presence, voice UX, announcements, HA timers | Wave 4 |
| Voice ID, kid mode, multi-room, smartring, wall tablet | Wave 4+ (far) |

Scheduled sequencing inside Mimir lives in ROADMAP (notably [Phase 11 — Future features](./ROADMAP.md)). This file does **not** invent ROADMAP scope; it only tracks candidates and ownership so promotion is mechanical.

---

## How to use

1. Capture or refine an idea here (status `parked` unless it already appears in ROADMAP).
2. When promoting into ROADMAP: copy **Tools + context** into the phase notes; set **Status** to `in-roadmap` and point **ROADMAP overlap** at the exact phase/bullet. Do not delete the entry.
3. When exit criteria are met: set **Status** to `done`, link the phase doc/ADR under **Evidence**, and keep the entry for history.
4. Prefer `TBD` or `none` over invented APIs, tool names, or ownership.

### Status legend

| Status | Meaning |
|--------|---------|
| `parked` | Idea only; not scheduled in ROADMAP |
| `partial` | A narrower slice already ships; remaining scope is listed under User stories / Open questions |
| `in-roadmap` | Explicitly listed in ROADMAP — **ROADMAP overlap** must cite the exact phase/bullet |
| `done` | Exit criteria met — cite evidence (phase doc / ADR) |

### Horizon (informational)

| Horizon | Meaning |
|---------|---------|
| `near` | Natural after Phase 10 voice / early Phase 11 leverage |
| `mid` | Needs sibling APIs, richer memory, or extra integrations |
| `far` | Multi-room, research-heavy, or hardware/companion-app dependent |

### Entry fields

Required: **Status**, **Horizon**, **Summary**, **Depends on**, **Source of truth**, **API ownership**, **Tools + context**, **ROADMAP overlap**.

Optional / conditional: **User stories**, **Open questions**, **Evidence** (required when `done`; recommended when `partial`), **Sensitivity** (when PII, finance, health, camera, or destructive writes are involved).

Use `none`, `TBD`, or `n/a` rather than filler.

### Ownership defaults (mesh)

| Capability cluster | Source of truth | Mimir role |
|--------------------|-----------------|------------|
| Inventory, shopping, recipes, packages, household calendar/tasks, people/contacts (as Homebase modules mature) | **Homebase** (Postgres) | Brain tools → Homebase LAN API (future); never duplicate as primary store |
| Expenses, payday cycles, net worth | **BudgetTracker** (SQLite on NAS) | Read/query tools first; writes only if BudgetTracker exposes safe APIs |
| Personal/knowledge notes, semantic note search | **Grimoire** (SQLite + LanceDB) | Capture / query tools; do not replace Grimoire’s local-first store |
| Movie catalogue, preference allowlist, conversation history | **Mimir brain** (SQLite) | Owner |
| Lights, sensors, media players, TTS speakers, presence | **Home Assistant** | Tools via HA after Phase 10; automation authoring = generate + user confirm, not silent write |
| Homebase budget module vs BudgetTracker | **Ambiguous** (CONNECTIONS §5) | Prefer BudgetTracker for deep money until §5 decides; document open questions |

**Shared scheduler / notifications:** timers, reminders, deferrals (“not now”), departure alerts, evening wind-down, announcements, presence message-relay, and ambient monologue all need a common execution contract (timezone, quiet hours, delivery channel, cancel, rate limits). Until that exists, treat **HA notify / TTS** and/or a brain-side worker as TBD shared dependency — do not invent three separate schedulers.

---

## 1. Smart home and presence

### Air quality sensor warnings

- **Status:** parked
- **Horizon:** near
- **Summary:** Warn when indoor (or outdoor) air quality crosses a threshold.
- **User stories:**
  - Air quality sensors warning (smart-home path).
  - Weather API expansion also covers air quality (see Weather air-quality expansion).
- **Depends on:** Phase 10 (HA conversation agent) for sensor entities; or weather tool expansion for outdoor AQ.
- **Source of truth:** Home Assistant sensor entities (indoor); Open-Meteo / weather provider (outdoor) — TBD which path is primary for “warning.”
- **API ownership:** HA exposes sensor state; Mimir reads via HA tool or weather tool. No writes.
- **Tools + context:** Proposed: `get_air_quality` or HA `get_state`; config thresholds; fail clear if sensor missing.
- **Open questions:** Indoor HA sensors vs outdoor weather AQ — one tool or two?
- **ROADMAP overlap:** none (related weather expansion is parked here; Phase 11 lists Buienradar rain nowcast, not AQ)
- **Sensitivity:** household environment data; keep out of default turn traces

### Per-room heating efficiency

- **Status:** parked
- **Horizon:** mid
- **Summary:** Report or advise on per-room heating efficiency from HA climate/sensor data.
- **User stories:**
  - Per-room heating efficiency insights.
- **Depends on:** Phase 10; HA climate + temperature entities; enough history for “efficiency” to mean something.
- **Source of truth:** Home Assistant (climate / temp / energy entities)
- **API ownership:** HA owns device state and history; Mimir is read/advise only unless a confirmed setpoint tool is added later.
- **Tools + context:** Proposed: HA climate/sensor query tool; prompt: room names from HA areas; fail if entities unmapped.
- **Open questions:** What metric is “efficiency” (runtime vs setpoint vs energy)?
- **ROADMAP overlap:** none (broader smart home is Phase 11)

### Home Assistant automation authoring

- **Status:** parked
- **Horizon:** mid
- **Summary:** Mimir drafts Home Assistant automations (scripts / YAML) from natural language.
- **User stories:**
  - Automated Home Assistant automation authoring.
  - Mimir writes scripts and YAML.
- **Depends on:** Phase 10; user confirmation before apply; ideally HA API or file-drop path TBD.
- **Source of truth:** Home Assistant automations store (YAML / UI storage)
- **API ownership:** Mimir **generates**; user (or confirmed tool) **applies** to HA. Never silent write of automations.
- **Tools + context:** Proposed: `draft_ha_automation` (returns YAML); optional `apply_ha_automation` behind confirmation; prompt: entity/area allowlist from config.
- **Open questions:** Apply via HA API vs show YAML for manual paste?
- **ROADMAP overlap:** none (Phase 11 “Smart home control” is control, not authoring)
- **Sensitivity:** side-effectful; **confirmation on destructive / apply actions** required

### Smart lighting control and scenes

- **Status:** in-roadmap
- **Horizon:** near
- **Summary:** Control lights (including IKEA) and trigger scenes such as “movie night.”
- **User stories:**
  - Control IKEA smart lights.
  - Scene triggers: “movie night” turns lighting to a certain setting.
- **Depends on:** Phase 10; lights exposed in HA (IKEA via ZHA/Matter/Tradfri bridge as configured).
- **Source of truth:** Home Assistant light / scene entities
- **API ownership:** HA owns device control; Mimir calls HA service tools (`light.turn_on`, `scene.turn_on`, etc.).
- **Tools + context:** Proposed: `ha_call_service` or typed `set_lights` / `activate_scene`; config: scene name map (“movie night” → scene entity_id); timeout + fail clear.
- **ROADMAP overlap:** [Phase 11 — Smart home control](./ROADMAP.md)
- **Sensitivity:** side-effectful; prefer confirm for whole-house scenes if desired

### Presence detection (phone on LAN)

- **Status:** parked
- **Horizon:** mid
- **Summary:** Infer whether someone is home from phone presence on the network; relay a spoken message when they return.
- **User stories:**
  - Phone-based presence: phone not on network → person most likely not home.
  - Message relay: when they get home they hear a message.
- **Depends on:** HA device_tracker / router integration; TTS announcement path; shared scheduler/notification contract.
- **Source of truth:** Home Assistant device_tracker / person entities; queued messages TBD (HA notify helper or brain queue)
- **API ownership:** HA owns presence state and TTS speakers; Mimir enqueues relay text and reads presence. Confirm before storing sensitive relay content if needed.
- **Tools + context:** Proposed: `get_presence`, `queue_return_message`; config: person ↔ device map; quiet hours; cancel/replace queued message.
- **Open questions:** Privacy of continuous presence tracking; false negatives when Wi‑Fi is off.
- **ROADMAP overlap:** none
- **Sensitivity:** location/presence PII; retention policy TBD

### Multi-room voice answer routing

- **Status:** parked
- **Horizon:** far
- **Summary:** Ask in one room and receive the answer on the correct room’s speaker.
- **User stories:**
  - Far future: multi-room support — ask in multiple rooms and get the answer in the correct one.
- **Depends on:** Phase 10+; Wyoming/HA satellites with known room; TTS target selection.
- **Source of truth:** Home Assistant media_player / assist satellite area mapping
- **API ownership:** HA owns satellite ↔ room; Mimir returns reply text; HA pipeline routes TTS to originating (or chosen) room.
- **Tools + context:** Mostly HA pipeline config + optional `announce` target; brain may need request metadata (source room).
- **ROADMAP overlap:** none
- **Open questions:** How source room is passed into the brain adapter.

---

## 2. Media (Jellyfin and beyond)

### Learned media preferences

- **Status:** partial
- **Horizon:** near
- **Summary:** Improve recommendations from observed taste beyond today’s small preference allowlist.
- **User stories:**
  - Learned preferences from watching / feedback over time.
- **Evidence (partial):** Phase 4 preferences (`favorite_genres`, `tone`) + Phase 5/8a catalogue and recent watches — not full “learned” model.
- **Depends on:** Catalogue sync; optional feedback signals (favorites, skips).
- **Source of truth:** Mimir brain SQLite (preferences + catalogue + watch signals)
- **API ownership:** Brain owns prefs and derived signals; Jellyfin remains library/watch SoT for playback state.
- **Tools + context:** Extend `get_preference` / `set_preference` allowlist or add derived-taste injection; re-run tool suite after prompt changes.
- **Open questions:** Explicit prefs vs implicit learning; avoid opaque profiles without user control.
- **ROADMAP overlap:** related to Phase 4 prefs + Phase 5/8a; richer learning not a named Phase 11 bullet

### Jellyfin playback control

- **Status:** parked
- **Horizon:** mid
- **Summary:** Play, pause, skip, and continue watching via Jellyfin (and/or HA media player).
- **User stories:**
  - Media control: play, pause, skip Jellyfin (if possible through API).
  - Continue watching X (needs series expansion for TV).
- **Depends on:** Jellyfin session/remote control API feasibility; or HA media_player pointed at Jellyfin/client; series in catalogue (see Series expansion).
- **Source of truth:** Jellyfin playback sessions / HA media_player
- **API ownership:** Jellyfin or HA executes transport controls; Mimir issues tool calls only. Confirm target client/TV.
- **Tools + context:** Proposed: `media_play`, `media_pause`, `media_next`, `continue_watching`; config: default player; fail if no active session.
- **Open questions:** Jellyfin API vs HA media_player as the control plane.
- **ROADMAP overlap:** [Phase 11 — Play music](./ROADMAP.md) (playback adjacent; movies/series control not spelled out)

### Series and continue watching

- **Status:** parked
- **Horizon:** mid
- **Summary:** Expand catalogue beyond movies so “continue watching” works for series.
- **User stories:**
  - Continue watching X — would need expansion to series.
- **Depends on:** Schema/sync beyond movies-only v1 (CONTEXT: Movie is films-only today).
- **Source of truth:** Jellyfin library → Mimir catalogue cache (extended)
- **API ownership:** Brain owns sync + cache; Jellyfin owns library.
- **Tools + context:** Extend sync + recommend/continue tools; prompt vocabulary for episode vs movie.
- **ROADMAP overlap:** none (movies locked for v1; series is backlog)
- **Open questions:** Episodes/seasons schema; Next Up vs resume position.

### Library availability check

- **Status:** parked
- **Horizon:** near
- **Summary:** Answer “do we have X in the library?”
- **User stories:**
  - Availability check: “do we have X in library?”
- **Depends on:** Catalogue sync (exists for movies).
- **Source of truth:** Mimir catalogue (movies today); Jellyfin for types not yet cached.
- **API ownership:** Brain search tool over catalogue; no Jellyfin live search required for movies if sync is fresh.
- **Tools + context:** Proposed: `library_has_title` / extend recommend search; say when sync is stale.
- **ROADMAP overlap:** none (builds on Phase 5 catalogue)

### Co-watcher recommendation profiles

- **Status:** parked
- **Horizon:** mid
- **Summary:** Recommend something suitable when watching with a named person.
- **User stories:**
  - User profiles for suggestions: watching a movie with person X — recommend me something.
- **Depends on:** Per-person taste data (prefs or Homebase people); still single-user brain until multi-profile.
- **Source of truth:** TBD — brain prefs keyed by person label vs Homebase people module
- **API ownership:** Mimir recommendation tool takes `with_person`; taste data SoT TBD.
- **Tools + context:** Extend recommend tools with `with_person`; prompt: intersection of tastes / avoid disliked genres.
- **Open questions:** Where person taste lives; relation to voice ID / multi-user.
- **ROADMAP overlap:** none (Phase 11 Voice ID / multi-user related)

### Mark movie as favorite

- **Status:** parked
- **Horizon:** near
- **Summary:** Mark a movie as favorite (brain and/or Jellyfin).
- **User stories:**
  - Mark movie as favorite.
- **Depends on:** Catalogue row identity; optional Jellyfin favorite API.
- **Source of truth:** TBD — Jellyfin UserData Favorite vs brain-side flag
- **API ownership:** Prefer writing through Jellyfin if API supports it so other clients see it; else brain SQLite + confirm.
- **Tools + context:** Proposed: `set_movie_favorite`; confirmation optional; fail if title ambiguous.
- **ROADMAP overlap:** none

### Runtime filter for recommendations

- **Status:** parked
- **Horizon:** near
- **Summary:** Filter picks by runtime, e.g. “something under 100 minutes.”
- **User stories:**
  - Runtime filter: “something under 100 minutes.”
- **Depends on:** Runtime on catalogue rows (already typical Jellyfin metadata).
- **Source of truth:** Mimir catalogue
- **API ownership:** Brain filter in recommend tool.
- **Tools + context:** Extend recommend args: `max_runtime_minutes`; prompt examples in system prompt / suite cases.
- **ROADMAP overlap:** none

### Music control (Jellyfin / Spotify)

- **Status:** in-roadmap
- **Horizon:** mid
- **Summary:** Play music via Jellyfin and/or Spotify.
- **User stories:**
  - Music control too?
  - Spotify perhaps.
- **Depends on:** Phase 10 for HA media players; Jellyfin music libraries or Spotify integration (network/account).
- **Source of truth:** Jellyfin music library and/or Spotify; playback via HA or vendor API
- **API ownership:** TBD between Jellyfin, Spotify, HA. Mimir should not own playlists as SoT.
- **Tools + context:** Align with Phase 11 play-music tool; config: default music player; timeouts.
- **Open questions:** Jellyfin-only vs Spotify; account/secret handling for Spotify.
- **ROADMAP overlap:** [Phase 11 — Play music](./ROADMAP.md)

### Household media statistics

- **Status:** parked
- **Horizon:** mid
- **Summary:** Household stats such as how many movies were watched this year.
- **User stories:**
  - Household statistics — amount of movies watched this year, etc.
- **Depends on:** Watch history in catalogue / Jellyfin userdata over time.
- **Source of truth:** Mimir catalogue watch fields + sync history; Jellyfin for authoritative playback dates if needed
- **API ownership:** Brain aggregates for the configured Jellyfin user; multi-person stats need multi-user later.
- **Tools + context:** Proposed: `media_stats`; prompt: year/period args; degrade if sync incomplete.
- **ROADMAP overlap:** none

---

## 3. Time, calendar, and travel

### Timers, reminders, and alarms

- **Status:** parked
- **Horizon:** near
- **Summary:** Start a timer, set a reminder, or set an alarm.
- **User stories:**
  - Timers, reminders, alarms — start a timer, set an alarm.
- **Depends on:** Shared scheduler/notification contract; HA timer/assist or brain worker + TTS/notify.
- **Source of truth:** TBD — HA timer helpers vs brain SQLite scheduled jobs
- **API ownership:** Executor owns fire time; Mimir creates/cancels via tools. Cancel must work with “never mind” / “laat maar.”
- **Tools + context:** Proposed: `start_timer`, `set_reminder`, `set_alarm`, `cancel_timer`; timezone from config; quiet hours.
- **ROADMAP overlap:** none
- **Sensitivity:** side-effectful notifications

### Calendar write access

- **Status:** in-roadmap
- **Horizon:** mid
- **Summary:** Mimir can create/update calendar events, not only read ICS feeds.
- **User stories:**
  - Expanded calendar — Mimir would be able to write to the calendar.
  - Create appointments (also listed under Homebase scheduling).
- **Depends on:** Phase 8d read-only ICS exists; write needs CalDAV/local bridge or Homebase calendar API.
- **Source of truth:** TBD — provider calendar vs Homebase calendar module (CONNECTIONS / Homebase modules)
- **API ownership:** Calendar provider or Homebase owns events; Mimir write tool with confirmation. ICS subscribe URLs stay read-only.
- **Tools + context:** Proposed: `create_calendar_event`, `find_open_slot`; config: writable calendar id; confirm before write.
- **Open questions:** Proton/ICS-only world vs Homebase calendar as write SoT.
- **ROADMAP overlap:** [Phase 11 — Calendar deepen](./ROADMAP.md) (“write access only if needed”)
- **Evidence (boundary):** Phase 8d = read-only Calendar feed ICS — see [`docs/phase8d-calendar.md`](./docs/phase8d-calendar.md)

### Departure alerts and winter ice scrape hint

- **Status:** parked
- **Horizon:** mid
- **Summary:** “Leave in X minutes for your appointment,” plus winter likelihood of needing to scrape ice off the car.
- **User stories:**
  - Departure alerts: “leave in X minutes for appointment.”
  - In winter when it has frozen, notify likelihood of having to scrape ice off the car.
- **Depends on:** Calendar read; travel-time estimate; weather (temp/frost); shared scheduler/notify.
- **Source of truth:** Calendar feed / writable calendar; weather tool; travel-time provider TBD
- **API ownership:** Mimir (or worker) computes leave-by time; HA/Discord notifies. No calendar mutation required for alerts.
- **Tools + context:** Proposed: worker or proactive hook using `get_calendar` + weather + `travel_time`; config: default commute origin; frost threshold °C.
- **ROADMAP overlap:** related to [Phase 11 — Proactive notifications](./ROADMAP.md) (not the same as Jellyfin new-episode watch)
- **Sensitivity:** location/schedule

### Travel time and traffic

- **Status:** parked
- **Horizon:** mid
- **Summary:** “How long does it take to get to location X?” — distance, traffic, travel time.
- **User stories:**
  - Travel time with kilometers, traffic conditions, travel time.
- **Depends on:** Network; routing provider TBD (privacy-sensitive).
- **Source of truth:** External routing/traffic API (TBD); home lat/long already in config for weather
- **API ownership:** Provider owns live traffic; Mimir tool wraps with timeout and clear failure.
- **Tools + context:** Proposed: `travel_time`; config: default origin; units km; fail offline.
- **Open questions:** Which provider (local-friendly)? Offline degrade?
- **ROADMAP overlap:** none
- **Sensitivity:** destination queries may be sensitive — minimize logging

### Public transport departures

- **Status:** parked
- **Horizon:** mid
- **Summary:** Next departures from Zwolle station, e.g. “next train to Nunspeet.”
- **User stories:**
  - Public transport departure times; default departure location Zwolle train station; “next train to Nunspeet.”
- **Depends on:** Network; NS / GTFS / third-party API TBD.
- **Source of truth:** Transit API (TBD)
- **API ownership:** Provider owns schedules; Mimir read-only tool; config default origin station.
- **Tools + context:** Proposed: `next_departures`; args: destination, origin default Zwolle; timeout; Dutch station names.
- **Open questions:** Official NS API vs open GTFS.
- **ROADMAP overlap:** none

### Sunrise, sunset, and moon phase

- **Status:** parked
- **Horizon:** near
- **Summary:** Report sun/moon times; optionally drive lighting automations.
- **User stories:**
  - Sunrise/sunset, moon phase.
  - Could tie into Homebase → automated lighting (e.g. if sunset is at X, turn on lights at Y).
- **Depends on:** Weather/astronomy source or HA sun integration; lighting via HA.
- **Source of truth:** Astronomy/weather provider or HA `sun` entity; lighting actions in HA
- **API ownership:** Read from weather/HA; write lighting only via HA tools with confirm/scene.
- **Tools + context:** Extend weather tool or `get_sun_moon`; optional link to lighting scenes.
- **ROADMAP overlap:** none (smart-home tie-in → Phase 11 smart home)

### Photoshoot logistics

- **Status:** parked
- **Horizon:** mid
- **Summary:** Plan a photoshoot: location, weather at location, travel time.
- **User stories:**
  - Photoshoot logistics — location, weather at location, travel time.
- **Depends on:** Weather for arbitrary lat/long; travel_time tool; optional calendar write.
- **Source of truth:** Composed from weather + routing tools; no new SoT
- **API ownership:** Mimir orchestrates existing tools; no dedicated photoshoot store.
- **Tools + context:** Prompt pattern / optional `plan_outing` composite; reuse weather + travel_time.
- **ROADMAP overlap:** none

---

## 4. Household mesh — Homebase

See [Homebase](../ProjectOverview/projects/Homebase.md) (`D:\Dev\Projects\Homebase`): inventory, shopping, tasks, calendar, recipes, delivery, smart home modules, etc. Mimir is the natural-language front door; **Homebase remains SoT** for household ops data ([CONNECTIONS §8](../ProjectOverview/CONNECTIONS.md)).

### Package tracking

- **Status:** parked
- **Horizon:** mid
- **Summary:** Ask about household package / delivery status via Homebase.
- **User stories:**
  - Package tracking.
- **Depends on:** Homebase Delivery module + LAN API (TBD — no shared API yet per CONNECTIONS).
- **Source of truth:** Homebase (Delivery module / Postgres)
- **API ownership:** Homebase serves read/update; Mimir `get_packages` / `update_package` tools. Confirm on status mutations.
- **Tools + context:** Proposed: `list_deliveries`; config: Homebase base URL + auth; timeout; fail if module disabled.
- **ROADMAP overlap:** none
- **Sensitivity:** address/tracking numbers — redact in logs

### Household inventory updates

- **Status:** parked
- **Horizon:** mid
- **Summary:** Query and update household inventory through Homebase.
- **User stories:**
  - Household inventory updating.
- **Depends on:** Homebase Inventory module + API.
- **Source of truth:** Homebase Inventory
- **API ownership:** Homebase mutates stock; Mimir tools with confirmation on decrements/deletes.
- **Tools + context:** Proposed: `inventory_search`, `inventory_update`; barcode flows may belong to Homebase/mobile later.
- **ROADMAP overlap:** none
- **Sensitivity:** household contents; confirmation on destructive updates

### Shopping list updates from anywhere

- **Status:** in-roadmap
- **Horizon:** near
- **Summary:** Add/check shopping list items from chat or voice via Homebase (or HA list — prefer Homebase when mesh exists).
- **User stories:**
  - Shopping list updates from anywhere.
- **Depends on:** Homebase Shopping module API or HA shopping list; Phase 10 for voice.
- **Source of truth:** **Homebase Shopping** preferred over HA list when integrated (avoid dual SoT).
- **API ownership:** Homebase owns list rows; Mimir append/check tools. If Phase 11 uses HA first, document migration to Homebase.
- **Tools + context:** Proposed: `shopping_add`, `shopping_list`; config endpoint; idempotent add.
- **Open questions:** ROADMAP Phase 11 says “HA or similar” — reconcile with Homebase as mesh SoT.
- **ROADMAP overlap:** [Phase 11 — Shopping lists](./ROADMAP.md)

### Countdown to holidays or events

- **Status:** parked
- **Horizon:** mid
- **Summary:** Countdown to holidays or household events.
- **User stories:**
  - Countdown to holidays or events.
- **Depends on:** Homebase calendar/events or Calendar feed; optional Homebase routines.
- **Source of truth:** Homebase events and/or Mimir Calendar feed
- **API ownership:** Read-only compose in Mimir; event SoT elsewhere.
- **Tools + context:** Proposed: `event_countdown` or prompt over calendar tools.
- **ROADMAP overlap:** none

### Wake-on-LAN

- **Status:** parked
- **Horizon:** mid
- **Summary:** Wake PCs and similar devices on the LAN.
- **User stories:**
  - Wake-on-LAN for PC etc.
- **Depends on:** HA wake-on-lan integration or Homebase/smart-home hook; MAC allowlist.
- **Source of truth:** Device registry in HA (or Homebase network docs) — MAC allowlist in config
- **API ownership:** HA (or script) sends magic packet; Mimir only calls allowlisted targets with confirmation.
- **Tools + context:** Proposed: `wake_device`; config allowlist name→MAC; confirm; fail if unknown device.
- **ROADMAP overlap:** none
- **Sensitivity:** side-effectful; strict allowlist

### People database (contacts, birthdays)

- **Status:** parked
- **Horizon:** mid
- **Summary:** Addresses, contact details, birthdays, and birthday reminders.
- **User stories:**
  - People database — addresses, contact details, birthdays.
  - Birthday reminders.
- **Depends on:** Homebase people/contacts capability (messages/visitor prefs exist; full CRM TBD) or separate store.
- **Source of truth:** **Homebase** preferred for household people data; TBD if module is complete enough
- **API ownership:** Homebase CRUD; Mimir read + reminder scheduling via shared notify path. Confirm on edits.
- **Tools + context:** Proposed: `get_person`, `list_birthdays`; worker for reminders; quiet hours.
- **Open questions:** Homebase module coverage vs thin brain table (avoid dual SoT).
- **ROADMAP overlap:** none
- **Sensitivity:** PII (addresses, contacts) — high; never dump full DB into prompts

### Scheduling (find slot / create appointment)

- **Status:** parked
- **Horizon:** mid
- **Summary:** Find an open spot in the schedule and create appointments.
- **User stories:**
  - Find open spot in schedule.
  - Create appointments.
- **Depends on:** Calendar write SoT (Homebase calendar vs external CalDAV); see Calendar write access.
- **Source of truth:** Same as calendar write SoT (TBD)
- **API ownership:** Calendar SoT writes events; Mimir proposes slots then confirmed create.
- **Tools + context:** Proposed: `find_open_slot`, `create_calendar_event`; always confirm create.
- **ROADMAP overlap:** [Phase 11 — Calendar deepen](./ROADMAP.md)

### To-do list

- **Status:** parked
- **Horizon:** mid
- **Summary:** Household to-dos via Homebase tasks.
- **User stories:**
  - To-do list.
- **Depends on:** Homebase Tasks module + API.
- **Source of truth:** Homebase Tasks
- **API ownership:** Homebase mutates tasks; Mimir add/complete/list tools; confirm completes/deletes if destructive.
- **Tools + context:** Proposed: `todo_list`, `todo_add`, `todo_complete`.
- **ROADMAP overlap:** none

### Recurring reminders in morning brief

- **Status:** partial
- **Horizon:** near
- **Summary:** Surface recurring reminders as part of the morning brief.
- **User stories:**
  - Recurring reminders — in good-morning feature.
- **Evidence (partial):** Phase 8e morning brief = weather + today’s schedule only (no news, no todos/reminders yet) — [`docs/phase8e-morning-brief.md`](./docs/phase8e-morning-brief.md).
- **Depends on:** Reminder SoT (Homebase tasks/routines or brain); morning brief prompt expansion.
- **Source of truth:** TBD — Homebase routines/tasks vs brain reminders
- **API ownership:** Read reminders into brief; do not mark complete without ask.
- **Tools + context:** Extend morning-brief discipline to call reminder/todo tool; keep brief short.
- **ROADMAP overlap:** Phase 8e done for weather+schedule; expansion not listed in Phase 11

### Recipe lookup and conversational cook-through

- **Status:** parked
- **Horizon:** mid
- **Summary:** Fetch a recipe from Homebase and walk through it step-by-step in conversation.
- **User stories:**
  - Recipe lookup.
  - Homebase-related: take recipe from Homebase and work through it; based on the conversation Mimir gives instructions.
- **Depends on:** Homebase Recipes module + API; optional timers tool.
- **Source of truth:** Homebase Recipes (inventory-linked ingredients live there)
- **API ownership:** Homebase owns recipe documents; Mimir reads and paces steps; timers via shared timer tool.
- **Tools + context:** Proposed: `get_recipe`, `recipe_step`; prompt: one step at a time for voice; link inventory shortages.
- **ROADMAP overlap:** none

### House manual for guests and sitters

- **Status:** parked
- **Horizon:** mid
- **Summary:** “Where can I find X?” / “How do I do X?” for guests and sitters.
- **User stories:**
  - House manual for guests or sitters.
  - Requires documents in Homebase; works with RAG over household documents.
- **Depends on:** Document store in Homebase; RAG pipeline (see Long-term facts and household RAG).
- **Source of truth:** Homebase documents / house manual content
- **API ownership:** Homebase stores docs; Mimir retrieval tool (RAG) read-only for guests; kid/guest mode may limit tools.
- **Tools + context:** Proposed: `house_manual_search`; restrict write tools in guest mode.
- **ROADMAP overlap:** none
- **Sensitivity:** physical security details (lockboxes, codes) — explicit allowlist of publishable docs

### Notes capture → Homebase pipeline

- **Status:** parked
- **Horizon:** mid
- **Summary:** “Mimir, write down X” — possibly file into Homebase.
- **User stories:**
  - Notes capture: “mimir, write down x.”
  - Notes to Homebase pipeline?
- **Depends on:** Homebase notes/messages module or tasks; conflict with Grimoire path (see Notes capture and Grimoire).
- **Source of truth:** TBD — Homebase vs Grimoire (open question)
- **API ownership:** Destination SoT owns stored note; Mimir capture tool routes by policy.
- **Tools + context:** Proposed: `capture_note` with `destination=homebase|grimoire|brain`; confirm destination if ambiguous.
- **Open questions:** Homebase vs Grimoire vs brain scratchpad — see §7.
- **ROADMAP overlap:** none

---

## 5. Household money — BudgetTracker

See [BudgetTracker](../ProjectOverview/projects/BudgetTracker.md) (`D:\Dev\Projects\BudgetTracker`): expenses, recurring bills, payday cycles, net worth on NAS. Homebase also has a lighter **Budget** module — ownership across money apps is unresolved in [CONNECTIONS §5](../ProjectOverview/CONNECTIONS.md).

### BudgetTracker integration

- **Status:** parked
- **Horizon:** mid
- **Summary:** Ask Mimir about household budget, cashflow, or log an expense via BudgetTracker.
- **User stories:**
  - BudgetTracker integration?
  - See BudgetTracker project.
- **Depends on:** BudgetTracker LAN HTTP API stability; auth; CONNECTIONS §5 decision on Homebase budget vs BudgetTracker.
- **Source of truth:** **BudgetTracker** for deep finance (expenses, payday, net worth) until §5 says otherwise; Homebase budget module stays light / TBD deep-link
- **API ownership:** BudgetTracker (`budget-server`) owns mutations and SQLite; Mimir is NL front door (read tools first; writes with confirmation). Discord may also query the mesh later (§8) — same SoT.
- **Tools + context:** Proposed: `budget_summary`, `list_expenses`, `add_expense` (confirm); config: base URL; never log amounts in turn traces by default.
- **Open questions:** Shared household-member/category taxonomy with Homebase; write API surface today vs needed.
- **ROADMAP overlap:** none
- **Sensitivity:** financial — high; confirm writes; redact logs

---

## 6. Notes, knowledge, and long-term memory

### Notes capture and Grimoire tie-in

- **Status:** parked
- **Horizon:** mid
- **Summary:** Capture a note by voice/chat; optionally land in Grimoire.
- **User stories:**
  - “Mimir, write down X.”
  - Grimoire tie-in?
  - Notes to Homebase pipeline? (cross-listed)
- **Depends on:** Grimoire local API/automation surface (TBD — desktop app today); or file drop / sync folder.
- **Source of truth:** **Grimoire** for personal/knowledge notes (SQLite + LanceDB); Homebase for household ops notes if routed there
- **API ownership:** Grimoire owns note records and embeddings; Mimir sends capture requests. No second full notes DB in the brain beyond scratch/long-term facts policy.
- **Tools + context:** Proposed: `capture_note`; destination policy in config; fail clear if Grimoire unreachable.
- **Open questions:** Desktop-only Grimoire vs headless sync on NAS; overlap with Homebase.
- **ROADMAP overlap:** none
- **Sensitivity:** note content may be sensitive

### Long-term facts, forget, RAG, reviews, decision logs

- **Status:** partial
- **Horizon:** mid
- **Summary:** Durable recallable household facts, selective forget, optional RAG over documents, annual review, decision logs.
- **User stories:**
  - Long-term facts: “appliance X was serviced in March,” recallable later.
  - Selective forget: forget what was said about X.
  - RAG over household data/documents?
  - Annual review: what changed this year.
  - Decision logs.
- **Evidence (partial):** Phase 4 = Conversation history window + allowlisted Preferences — not long-term arbitrary facts, RAG, or forget-by-topic. History compaction is Phase 11.
- **Depends on:** Memory model design; document store (Homebase / files); embeddings policy (Concept: vectors only when stuffing fails — apply same discipline).
- **Source of truth:** TBD split — brain SQLite for short structured facts; Homebase/Grimoire/docs for documents; embeddings index TBD
- **API ownership:** Brain tools `remember_fact`, `forget_fact`, `search_household_docs`; document SoT stays external. Forget must be explicit and scoped.
- **Tools + context:** Fact schema (subject, predicate, timestamp); RAG retrieval caps; annual review = aggregate query; decision log = append-only store TBD.
- **Open questions:** “Is a proper memory model already on the roadmap?” — Phase 4 memory + Phase 11 history compaction only; full fact/RAG model is backlog here.
- **ROADMAP overlap:** [Phase 11 — History compaction / summarization](./ROADMAP.md) (related, not equivalent); vector search for Jellyfin catalogue is a separate Phase 11 bullet
- **Sensitivity:** high — selective forget + retention policy required

### Home network documentation

- **Status:** parked
- **Horizon:** mid
- **Summary:** List of IPs, devices, what is where.
- **User stories:**
  - Home network documentation — IPs, devices, what is where.
- **Depends on:** Document store (Homebase or curated markdown); optional RAG.
- **Source of truth:** Curated docs (prefer Homebase or versioned file in NAS) — **not** auto-scraped without review
- **API ownership:** Doc SoT; Mimir read-only search. Updates via confirmed edit or human PR to docs.
- **Tools + context:** Reuse `house_manual_search` / RAG with a network-docs corpus tag.
- **ROADMAP overlap:** none
- **Sensitivity:** infrastructure map — high; restrict guest/kid mode

### Wikipedia lookup

- **Status:** parked
- **Horizon:** near
- **Summary:** Look up a topic on Wikipedia.
- **User stories:**
  - Wikipedia lookup.
- **Depends on:** Network; MediaWiki API; optional Grimoire ZIM path for offline (Grimoire has optional Wikipedia ZIM — separate app).
- **Source of truth:** Wikipedia (online) or local ZIM (if wired later)
- **API ownership:** Read-only tool in brain; cite title/URL; timeout.
- **Tools + context:** Proposed: `wikipedia_lookup`; language prefer NL/EN from prefs; no write.
- **ROADMAP overlap:** none

### Web search

- **Status:** parked
- **Horizon:** mid
- **Summary:** General web search to support other features.
- **User stories:**
  - Web search — could tie in to other features.
- **Depends on:** Network; search API TBD; privacy policy.
- **Source of truth:** Search provider (TBD)
- **API ownership:** Provider returns results; Mimir tool with strict timeouts and citation discipline.
- **Tools + context:** Proposed: `web_search`; config: provider keys in env; disable when offline.
- **Open questions:** Provider choice; when to force search vs refuse.
- **ROADMAP overlap:** none
- **Sensitivity:** query privacy

### Local news

- **Status:** parked
- **Horizon:** mid
- **Summary:** Local news briefing (not part of current morning brief).
- **User stories:**
  - Local news.
- **Depends on:** News source/API TBD; morning brief expansion optional.
- **Source of truth:** External news feed (TBD)
- **API ownership:** Read-only ingest tool; do not persist full articles unless user saves a note.
- **Tools + context:** Proposed: `local_news`; config: region; Phase 8e explicitly excluded news — keep opt-in.
- **ROADMAP overlap:** none (Phase 8e: weather + today’s schedule **only**, no news)

### Random facts

- **Status:** parked
- **Horizon:** near
- **Summary:** Occasional random fact on request (or tightly gated ambient use).
- **User stories:**
  - Random facts.
- **Depends on:** Fact source TBD (local list vs Wikipedia).
- **Source of truth:** Local curated list preferred for offline; else Wikipedia tool
- **API ownership:** Brain serves local list; no external SoT required for v0.
- **Tools + context:** Proposed: `random_fact` or prompt-only with curated file.
- **ROADMAP overlap:** none

### Reading list / bookmark capture

- **Status:** parked
- **Horizon:** mid
- **Summary:** Capture bookmarks / reading list items.
- **User stories:**
  - Reading list, bookmark capture.
- **Depends on:** SoT TBD — Grimoire vs Homebase vs brain table.
- **Source of truth:** TBD (lean Grimoire for personal reading list)
- **API ownership:** Destination SoT; Mimir `add_bookmark` tool.
- **Tools + context:** URL + title + tags; dedupe by URL.
- **Open questions:** Same destination policy as notes capture.
- **ROADMAP overlap:** none

### Shazam-like music recognition

- **Status:** parked
- **Horizon:** far
- **Summary:** Identify playing music from audio.
- **User stories:**
  - Shazam-like capabilities?
- **Depends on:** Mic access (HA voice satellite or companion app); recognition API or local model; network likely.
- **Source of truth:** Recognition provider TBD
- **API ownership:** Provider identifies track; Mimir returns metadata. Audio snippets ephemeral.
- **Tools + context:** TBD; requires audio pipeline beyond text chat.
- **Open questions:** Feasible locally? Privacy of audio upload?
- **ROADMAP overlap:** none
- **Sensitivity:** audio — high

---

## 7. Notifications and voice UX

### Conditional / proactive notifications

- **Status:** in-roadmap
- **Horizon:** near
- **Summary:** Notify on conditions such as “rain starts in an hour.”
- **User stories:**
  - Conditional notifications: “rain starts in an hour.”
- **Depends on:** Shared scheduler; weather/nowcast; HA notify or Discord send (Phase 8f).
- **Source of truth:** Weather provider for condition; notification sink HA/Discord
- **API ownership:** Worker evaluates rules; sinks deliver. Mimir may register rules via tools.
- **Tools + context:** Align with Phase 11 proactive notifications + [Phase 11 — Buienradar / Buienalarm rain nowcast](./ROADMAP.md); config: notify channel allowlist.
- **ROADMAP overlap:** [Phase 11 — Proactive notifications](./ROADMAP.md); [Phase 11 — Buienradar / Buienalarm](./ROADMAP.md)
- **Sensitivity:** side-effectful; rate-limit

### Evening wind-down

- **Status:** parked
- **Horizon:** near
- **Summary:** “Good night” brief: tomorrow’s first schedule items, weather, todos.
- **User stories:**
  - Evening wind-down: say good night and tomorrow’s first schedule items, weather, todos.
- **Depends on:** Calendar + weather tools (exist); todos from Homebase (TBD); phrase trigger like morning brief.
- **Source of truth:** Calendar feed + weather cache + todo SoT
- **API ownership:** Same as morning brief — phrase-triggered chat, not proactive push (unless user opts into notify).
- **Tools + context:** Mirror Phase 8e pattern; prompt phrases “good night” / Dutch equivalent; keep short.
- **ROADMAP overlap:** none (morning brief Phase 8e is the pattern to copy)

### Announcements (TTS to rooms)

- **Status:** parked
- **Horizon:** near
- **Summary:** Trigger TTS to a specific speaker/room or the whole house.
- **User stories:**
  - Announcements — TTS to a specific speaker/room or entire house.
- **Depends on:** Phase 10; HA TTS / media_player per room.
- **Source of truth:** HA speakers / areas
- **API ownership:** HA plays audio; Mimir `announce` tool with room allowlist.
- **Tools + context:** Proposed: `announce`; args: message, room|all; confirm for all-house; quiet hours.
- **ROADMAP overlap:** none (enabled by Phase 10)
- **Sensitivity:** side-effectful

### Cancel current voice command (“never mind” / “laat maar”)

- **Status:** parked
- **Horizon:** near
- **Summary:** Cancel the in-flight voice command.
- **User stories:**
  - “never mind” / “laat maar” cancels current voice command.
- **Depends on:** Phase 10 Assist pipeline; cancel signaling to brain/HA.
- **Source of truth:** n/a (control plane)
- **API ownership:** HA voice pipeline should abort STT/TTS; brain should abort tool loop if mid-turn.
- **Tools + context:** Pipeline keyword / intent; Dutch **laat maar** and English **never mind**; no new SoT.
- **ROADMAP overlap:** none (Phase 10 concern)
- **Open questions:** Cancel mid-tool vs mid-TTS only.

### “Not now” with optional defer

- **Status:** parked
- **Horizon:** near
- **Summary:** Dismiss a trigger when it’s a bad moment; optionally redo in X minutes.
- **User stories:**
  - “not now” when Mimir is triggered and it is not a good moment.
  - Optional: do this in X minutes.
- **Depends on:** Shared scheduler; Phase 10 triggers / proactive rules.
- **Source of truth:** Deferred job queue TBD (brain or HA)
- **API ownership:** Scheduler owns defer; Mimir records snooze.
- **Tools + context:** Proposed: `snooze_interaction`; quiet hours interaction with presence.
- **ROADMAP overlap:** none

### Confirmation on destructive actions

- **Status:** parked
- **Horizon:** near
- **Summary:** Require confirmation before destructive or hard-to-undo actions.
- **User stories:**
  - Confirmation on destructive actions — prevents unwanted deletion of data.
- **Depends on:** Tool-loop policy in brain (all write tools).
- **Source of truth:** n/a (policy)
- **API ownership:** **Brain enforces** confirm-before-execute for destructive tools regardless of model wording; sibling SoTs still perform the delete after confirm.
- **Tools + context:** Two-phase tools or `confirm_token`; suite cases for refuse-without-confirm.
- **ROADMAP overlap:** none
- **Sensitivity:** safety control — applies to Homebase/BudgetTracker/HA writes

### Ambient monologue

- **Status:** parked
- **Horizon:** far
- **Summary:** Occasional unprompted dry observation, strictly rate-limited, only under specific circumstances.
- **User stories:**
  - Ambient monologue — occasional unprompted dry observation, strictly rate-limited.
  - Triggered by very specific circumstances.
- **Depends on:** Proactive notify path; personality prompt; hard rate limits.
- **Source of truth:** Trigger rules in config; no user data SoT
- **API ownership:** Worker may emit rare TTS/chat line via HA; default **off**.
- **Tools + context:** Config: max N/day, allowlisted triggers only; never during quiet hours.
- **ROADMAP overlap:** none
- **Open questions:** Exact trigger list (must stay narrow).

---

## 8. Identity, memory, and profiles

### Per-user profiles and preference memory

- **Status:** partial
- **Horizon:** mid
- **Summary:** Remember preferences per person; summarize what Mimir knows about someone.
- **User stories:**
  - Per-user profile — remember preferences.
  - Summary of what Mimir knows per person.
  - Proper memory model — is this on the roadmap already?
- **Evidence (partial):** Phase 0 locked **single-user** for v1; Phase 4 Preferences allowlist + history window for one profile.
- **Depends on:** Multi-profile schema decision; richer memory (facts) optional.
- **Source of truth:** Brain SQLite profiles/prefs when multi-user unlocks; today single allowlist
- **API ownership:** Brain owns prefs; expose get/set; “what you know” is a read-only summary tool.
- **Tools + context:** `summarize_known_profile`; expand allowlist carefully; migrations sticky (AGENTS.md).
- **Open questions:** Memory model beyond Phase 4 — backlog here; compaction in Phase 11.
- **ROADMAP overlap:** Phase 0 single-user lock; [Phase 11 — Voice ID / multi-user](./ROADMAP.md)

### Voice ID

- **Status:** in-roadmap
- **Horizon:** far
- **Summary:** Identify who is speaking; hardest identity problem in the project; needs research.
- **User stories:**
  - Voice ID — might be the hardest thing of the entire project; requires research.
- **Depends on:** Phase 10 audio path; research spike; privacy review; likely multi-profile.
- **Source of truth:** Voiceprint store TBD (brain or HA) — high sensitivity
- **API ownership:** TBD; Mimir must not treat unverified ID as authorization for destructive tools.
- **Tools + context:** Research spike doc before implementation; suite for mis-ID failure modes.
- **ROADMAP overlap:** [Phase 11 — Voice ID / multi-user](./ROADMAP.md)
- **Sensitivity:** biometric-like — highest; explicit enrollment; confirm destructive actions regardless

### Kid mode

- **Status:** parked
- **Horizon:** far
- **Summary:** Reduced tool set for kids; probably requires voice ID.
- **User stories:**
  - Kid mode — less tools available; probably requires voice ID.
- **Depends on:** Voice ID or explicit profile switch; tool allowlists per profile.
- **Source of truth:** Brain policy config per profile
- **API ownership:** Brain enforces allowlist (model cannot bypass by asking).
- **Tools + context:** Config: `kid` profile tool denylist (HA unlock, budget, WoL, network docs, etc.).
- **ROADMAP overlap:** related to Phase 11 multi-user

### Email reading

- **Status:** in-roadmap
- **Horizon:** mid
- **Summary:** Read email (provider-agnostic if practical).
- **User stories:**
  - Reading emails.
- **Depends on:** Provider API/IMAP; secrets in env; not scheduled beyond backlog bullet.
- **Source of truth:** Mail provider
- **API ownership:** Provider owns mailboxes; Mimir read-only tools first; no send unless separately approved.
- **Tools + context:** Per Phase 11 email read; timeout; summarize not dump full inbox into context.
- **ROADMAP overlap:** [Phase 11 — Email read](./ROADMAP.md)
- **Sensitivity:** high — PII

### Conversation summarisation

- **Status:** in-roadmap
- **Horizon:** mid
- **Summary:** Summarise long conversations to avoid context clutter.
- **User stories:**
  - Conversation summarisation — avoid context clutter on longer conversation.
- **Depends on:** History window limits (`memory.history_pairs`, `num_ctx`).
- **Source of truth:** Brain SQLite Messages; summary artifact TBD in SQLite
- **API ownership:** Brain compaction job/tool; Messages remain canonical.
- **Tools + context:** Per Phase 11 history compaction; prompt: summary injection replaces oldest pairs.
- **ROADMAP overlap:** [Phase 11 — History compaction / summarization](./ROADMAP.md)

---

## 9. Health and wearables

### Smartring connection

- **Status:** parked
- **Horizon:** far
- **Summary:** Use ring data for evening step count and morning sleep summary; sync at wake.
- **User stories:**
  - Smartring connection.
  - Step count for good-evening feature.
  - Sleep summary at wake up.
  - Sync at wake up?
  - Requires companion app (likely).
- **Depends on:** Vendor companion app / API; evening wind-down + morning brief hooks; network.
- **Source of truth:** Vendor cloud or companion DB (TBD) — Mimir does not own health raw data long-term without policy
- **API ownership:** Companion syncs; Mimir reads daily summaries via adapter. No clinical claims.
- **Tools + context:** Proposed: `get_sleep_summary`, `get_steps`; config: enable flag default off.
- **Open questions:** Which ring/vendor; local bridge vs cloud.
- **ROADMAP overlap:** none
- **Sensitivity:** health — highest; minimize retention

---

## 10. Clients and operations

### Mobile companion app (camera)

- **Status:** parked
- **Horizon:** far
- **Summary:** Mobile companion giving Mimir camera capabilities (note scanning, receipt scanning).
- **User stories:**
  - Mobile companion app.
  - Camera capabilities — note scanning, receipt scanning.
- **Depends on:** New client app; brain upload endpoint; OCR; routing of receipts to BudgetTracker / notes to Grimoire|Homebase.
- **Source of truth:** Scanned text → destination SoT (BudgetTracker for receipts, Grimoire/Homebase for notes)
- **API ownership:** Companion captures; brain OCR/tool routes; destination APIs mutate. Confirm before expense create.
- **Tools + context:** Upload + `import_receipt` / `import_note_image`; auth token; size limits.
- **ROADMAP overlap:** none
- **Sensitivity:** camera + receipts (financial) — high

### Wall-mounted tablet (Mimir + Homebase)

- **Status:** parked
- **Horizon:** far
- **Summary:** Wall tablet UI combining Mimir and Homebase.
- **User stories:**
  - Wall-mounted tablet — Mimir + Homebase.
- **Depends on:** Homebase PWA; Mimir chat client or HA dashboard; LAN auth.
- **Source of truth:** n/a (presentation); data remains in brain + Homebase
- **API ownership:** Homebase PWA + Mimir HTTP API; tablet is a client only (AGENTS: clients are front doors).
- **Tools + context:** Deploy notes only; no brain logic on tablet.
- **ROADMAP overlap:** none

### Web dashboard (logs, history, system state)

- **Status:** parked
- **Horizon:** mid
- **Summary:** Dashboard for logs, history, and system state.
- **User stories:**
  - Web dashboard for logs, history, system state.
- **Depends on:** Turn traces / SQLite; host-only or LAN auth (CONTEXT: host-only for ops endpoints today).
- **Source of truth:** Brain logs + SQLite + `/health`
- **API ownership:** Brain serves read-only ops UI or JSON; do not expose publicly.
- **Tools + context:** Optional static ops UI; reuse turn JSONL; auth required on LAN.
- **ROADMAP overlap:** none (Phase 9 packaging adjacent)

### Auto backups to NAS

- **Status:** parked
- **Horizon:** mid
- **Summary:** Automatic backups of Mimir data to the NAS.
- **User stories:**
  - Auto backups to NAS.
- **Depends on:** Phase 9 volume layout; NAS path/SMB/rsync; schedule.
- **Source of truth:** Backup copies on NAS; live SoT remains brain data dir
- **API ownership:** Ops scripts / compose sidecar; not an LLM tool.
- **Tools + context:** Document in Phase 9 backup note; cron/rsync; test restore.
- **ROADMAP overlap:** related to [Phase 9 — Deployment packaging](./ROADMAP.md) backup note

### Weather air-quality expansion

- **Status:** parked
- **Horizon:** near
- **Summary:** Expand weather tooling with air quality (and related).
- **User stories:**
  - Weather API expansion — air quality etc.
- **Depends on:** Open-Meteo AQ endpoints or KNMI; existing weather tool.
- **Source of truth:** Weather provider; Forecast cache pattern may extend
- **API ownership:** Brain weather tool read-only (already owns weather integration).
- **Tools + context:** Extend `get_weather` or add `get_air_quality`; degrade offline via cache if applicable.
- **ROADMAP overlap:** none (Phase 11 rain nowcast is separate)

### Currency conversion

- **Status:** parked
- **Horizon:** near
- **Summary:** Convert between currencies.
- **User stories:**
  - Currency conversion.
- **Depends on:** Network rates API or ECB feed; offline fallback TBD.
- **Source of truth:** Rates provider (TBD)
- **API ownership:** Read-only tool; display EUR with `nl-NL` awareness (BudgetTracker convention) when relevant.
- **Tools + context:** Proposed: `convert_currency`; timeout; show rate date.
- **ROADMAP overlap:** none

---

## 11. Assistant meta

### Capability discovery (“what can you do”)

- **Status:** parked
- **Horizon:** near
- **Summary:** Explain available capabilities and how a tool works.
- **User stories:**
  - “What can you do.”
  - Also explain how a tool or capability works.
- **Depends on:** Registry of enabled tools/features from config.
- **Source of truth:** Brain tool registry + this backlog / ROADMAP for “coming later”
- **API ownership:** Brain `list_capabilities` or prompt section generated from enabled tools only (don’t advertise parked features as live).
- **Tools + context:** Auto-generate from tool schemas; distinguish live vs planned.
- **ROADMAP overlap:** none

### Usage stats, milestones, and Mimir’s birthday

- **Status:** parked
- **Horizon:** mid
- **Summary:** Usage statistics, congratulatory milestones, and remembering Mimir’s own birthday.
- **User stories:**
  - Usage stats.
  - Congrats on milestones from Mimir.
  - Remembers his own birthday — **26-08-2026**.
- **Depends on:** Turn traces / SQLite counters.
- **Source of truth:** Brain observability DB/JSONL; birthday constant in config or prompt
- **API ownership:** Brain owns counters; no sibling.
- **Tools + context:** Proposed: `usage_stats`; inject birthday **2026-08-26** into prompt on that date.
- **ROADMAP overlap:** none

### Repeat last response

- **Status:** parked
- **Horizon:** near
- **Summary:** Repeat the last assistant response (useful for voice).
- **User stories:**
  - Repeat last response.
- **Depends on:** Phase 10 nice-to-have; works in chat from history.
- **Source of truth:** Brain Messages / in-memory last TTS text
- **API ownership:** Brain returns last final assistant Message; HA may re-TTS.
- **Tools + context:** Intent routing without new model call when possible.
- **ROADMAP overlap:** none

### Simplified language (ELI5)

- **Status:** parked
- **Horizon:** near
- **Summary:** Explain in simplified language / ELI5 on request.
- **User stories:**
  - Simplified language — ELI5.
- **Depends on:** Prompt discipline only.
- **Source of truth:** n/a
- **API ownership:** Prompt / tone preference; optional pref key later.
- **Tools + context:** System prompt instruction; no tool required.
- **ROADMAP overlap:** none

### Explain yourself (rationale / tool use)

- **Status:** parked
- **Horizon:** near
- **Summary:** Short rationale for a response or tool call — not hidden chain-of-thought dump.
- **User stories:**
  - Explain yourself — reasoning behind response/tool call.
- **Depends on:** Tool-loop visibility; turn trace metadata.
- **Source of truth:** Last turn’s tool names/args (not full chain-of-thought)
- **API ownership:** Brain summarizes tool trace to user; `think: false` remains default.
- **Tools + context:** Prefer structured “I called X because Y”; do not expose raw hidden reasoning channels.
- **ROADMAP overlap:** none

### Confidence signaling

- **Status:** parked
- **Horizon:** mid
- **Summary:** Signal low/high confidence when appropriate.
- **User stories:**
  - Confidence signaling.
- **Depends on:** Prompt policy; optional model scores if ever available.
- **Source of truth:** n/a
- **API ownership:** Prompt-level; tools should return explicit uncertainty (stale sync, missing entity).
- **Tools + context:** Tool results include `confidence`/`stale` flags where relevant.
- **ROADMAP overlap:** none

### Conversation export and search

- **Status:** parked
- **Horizon:** mid
- **Summary:** Export or search past conversations (“what did I ask X time ago”).
- **User stories:**
  - Conversation export/search — find out what you asked X time ago.
- **Depends on:** SQLite Messages; TUI `/history` exists for resume (not full-text search).
- **Source of truth:** Brain SQLite Conversations/Messages
- **API ownership:** Brain search/export API; client displays. Host-only or auth.
- **Tools + context:** Proposed: `search_conversations` + export endpoint; privacy filters.
- **ROADMAP overlap:** none (Phase 8b is resume, not search)
- **Sensitivity:** conversation content

### A/B model comparison and regression suite

- **Status:** parked
- **Horizon:** mid
- **Summary:** Compare models for the household; keep regression suite green.
- **User stories:**
  - A/B model comparison to find the right model for the household.
  - Regression test suite.
- **Depends on:** Existing `scripts/tool_call_suite.py`; multi-model config.
- **Source of truth:** Suite results docs; Ollama model tags
- **API ownership:** Ops/scripts; not a user-facing chat feature first.
- **Tools + context:** Run suite per model; document viability ≥80% bar (AGENTS.md).
- **ROADMAP overlap:** standing suite already required on model changes; A/B UX is extra

### TTS speed settings

- **Status:** parked
- **Horizon:** near
- **Summary:** Talk faster or slower.
- **User stories:**
  - Speed settings — talk faster or slower.
- **Depends on:** Phase 10 Piper/TTS settings in HA.
- **Source of truth:** HA TTS voice configuration / preference
- **API ownership:** HA owns TTS rate; Mimir may store a preference and pass to HA if API allows.
- **Tools + context:** Pref `tts_rate` or HA automation; voice-only.
- **ROADMAP overlap:** none (Phase 10)

### Discussions (two-sided framing)

- **Status:** parked
- **Horizon:** near
- **Summary:** Lay out two sides of a problem on request.
- **User stories:**
  - Discussions — Mimir lays out two sides of a problem.
- **Depends on:** Prompt pattern only.
- **Source of truth:** n/a
- **API ownership:** Prompt; no tool.
- **Tools + context:** System prompt example for “steelman both sides.”
- **ROADMAP overlap:** none

---

## Coverage index (original notes → entries)

Lossless map from the pre-rewrite notes. If you add ideas, extend this table.

| Original note | Entry |
|---------------|--------|
| Smart home / AQ sensors warning | Air quality sensor warnings |
| Per room heating efficiency | Per-room heating efficiency |
| HA automation authoring / writes scripts+yaml | Home Assistant automation authoring |
| Control IKEA lights / movie night scenes | Smart lighting control and scenes |
| Jellyfin learned preferences | Learned media preferences |
| Music control / Spotify? | Music control (Jellyfin / Spotify) |
| Play/pause/skip Jellyfin | Jellyfin playback control |
| Continue watching / series expansion | Series and continue watching; Jellyfin playback control |
| Availability check | Library availability check |
| Co-watcher profiles | Co-watcher recommendation profiles |
| Mark favorite | Mark movie as favorite |
| Runtime filter | Runtime filter for recommendations |
| Timers / reminders / alarms | Timers, reminders, and alarms |
| Calendar write | Calendar write access |
| Departure alerts | Departure alerts and winter ice scrape hint |
| Winter ice scrape | Departure alerts and winter ice scrape hint |
| Homebase package tracking | Package tracking |
| Inventory updating | Household inventory updates |
| Shopping list from anywhere | Shopping list updates from anywhere |
| Holiday/event countdown | Countdown to holidays or events |
| Wake-on-LAN | Wake-on-LAN |
| People DB / addresses / contacts / birthdays / birthday reminders | People database (contacts, birthdays) |
| Find open spot / create appointments | Scheduling (find slot / create appointment) |
| To-do list | To-do list |
| Recurring reminders in good morning | Recurring reminders in morning brief |
| Recipe lookup / cook-through | Recipe lookup and conversational cook-through |
| Sunrise/sunset/moon → lighting | Sunrise, sunset, and moon phase |
| Public transport Zwolle → Nunspeet | Public transport departures |
| BudgetTracker integration? | BudgetTracker integration |
| Local news | Local news |
| Wikipedia | Wikipedia lookup |
| Shazam-like? | Shazam-like music recognition |
| Confirmation on destructive actions | Confirmation on destructive actions |
| Currency conversion | Currency conversion |
| never mind / laat maar | Cancel current voice command |
| not now + defer | “Not now” with optional defer |
| Conversation summarisation | Conversation summarisation |
| Per-user profile / prefs | Per-user profiles and preference memory |
| Voice ID + research | Voice ID |
| Reading emails | Email reading |
| Summary of what Mimir knows / memory model? | Per-user profiles; Long-term facts… |
| Conditional notifications / rain in an hour | Conditional / proactive notifications |
| Notes capture / Homebase? / Grimoire? | Notes capture → Homebase; Notes capture and Grimoire |
| Long-term facts / forget / RAG / annual review / decision logs | Long-term facts, forget, RAG, reviews, decision logs |
| Travel time / km / traffic | Travel time and traffic |
| Weather API expansion / AQ | Weather air-quality expansion |
| Web search | Web search |
| Multi-room answers | Multi-room voice answer routing |
| Evening wind-down | Evening wind-down |
| Announcements TTS | Announcements (TTS to rooms) |
| Photoshoot logistics | Photoshoot logistics |
| Reading list / bookmarks | Reading list / bookmark capture |
| Smartring steps/sleep/sync/companion | Smartring connection |
| House manual guests/sitters | House manual for guests and sitters |
| Home network documentation | Home network documentation |
| What can you do / explain tool | Capability discovery |
| Usage stats / milestones / birthday 26-08-2026 | Usage stats, milestones, and Mimir’s birthday |
| Repeat last response | Repeat last response |
| ELI5 | Simplified language (ELI5) |
| Explain yourself | Explain yourself (rationale / tool use) |
| Confidence signaling | Confidence signaling |
| Conversation export/search | Conversation export and search |
| A/B models / regression suite | A/B model comparison and regression suite |
| Talk faster/slower | TTS speed settings |
| Random facts | Random facts |
| Household stats / movies watched this year | Household media statistics |
| Mobile companion / camera / notes+receipts | Mobile companion app (camera) |
| Kid mode | Kid mode |
| Discussions two sides | Discussions (two-sided framing) |
| Presence phone LAN + message relay | Presence detection (phone on LAN) |
| Ambient monologue | Ambient monologue |
| Auto backups to NAS | Auto backups to NAS |
| Web dashboard | Web dashboard (logs, history, system state) |
| Wall tablet Mimir + Homebase | Wall-mounted tablet (Mimir + Homebase) |
