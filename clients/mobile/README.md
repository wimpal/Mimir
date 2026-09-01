# Mimir Android client (T-024 / T-025)

Thin Kotlin + Jetpack Compose chat client for the Mimir brain. Same conversation API
as the desktop TUI — brain only, no MCP or service calls. **T-025** adds push-to-talk
(STT → chat → TTS) on the same tool loop as typed chat.

## Prerequisites

- **Android Studio** Ladybug (2024.2+) or newer
- **JDK 17** (bundled with Android Studio)
- Operator Android phone **API 26+**
- Brain running on PC with LAN bind — see [Mimir README LAN section](../../README.md#lan-access-m5-mobile)
- Brain voice endpoints (`/v1/stt`, `/v1/tts`) from T-023 enabled on the PC

## Build

Open `clients/mobile/` in Android Studio and **Sync Project**, or from this directory:

```powershell
cd D:\Dev\Projects\Mimir\clients\mobile
.\gradlew.bat assembleDebug
```

Debug APK output:

```
app/build/outputs/apk/debug/app-debug.apk
```

Unit tests (ConfirmationDetector):

```powershell
.\gradlew.bat testDebugUnitTest
```

## Install

```powershell
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

Or copy the APK to the phone and install (enable “Install unknown apps” for the file manager).

## Configure

1. PC brain: `runtime.host: 0.0.0.0`, `auth.mode: token`, `MIMIR_CLIENT_TOKEN` in `.env`
2. Phone on **Archer Wi‑Fi** (`192.168.0.x`) — not T-56 Wi‑Fi alone (see below)
3. Discover PC IP: `ipconfig` → IPv4 (e.g. `192.168.0.42`)
4. In app **Settings**:
   - **Home URL (LAN):** `http://192.168.0.42:8000` — used on home Wi‑Fi
   - **Away URL (Tailscale):** `http://100.x.y.z:8000` — PC Tailscale IP; used when LAN unreachable
   - **Bearer token:** same value as TUI `.env` (`MIMIR_CLIENT_TOKEN`)
5. Tap **Save & test connection** → health indicator green (shows which URL is active)
6. **Continue to chat**

The app **probes LAN first**, then falls back to the away URL — no manual switching.

### Home network (Archer Wi‑Fi only for LAN)

Operator household: **Zyxel T-56** → **TP-Link Archer**. PC brain and NAS are on Archer
LAN (`192.168.0.0/24`). **Home URL works only on Archer Wi‑Fi.** On T-56 Wi‑Fi
(`192.168.1.x`) or mobile data, the LAN probe fails and the app uses **Away URL**
(Tailscale must be on for away access).

### Away from home (M7 — Tailscale)

When not on home Wi‑Fi, the brain is reachable only over the household **Tailscale
tailnet** (no port-forward, no public URL). See
[`project-control-heim/docs/runbook-tailnet.md`](../../../project-control-heim/docs/runbook-tailnet.md).

1. Enable **Tailscale** on the phone (same household account as the PC).
2. Set **Away URL** to the PC's **100.x** Tailscale IP (Tailscale app → tap PC).
   MagicDNS (`.ts.net`) works in the browser but often fails in-app — use the IP.
3. Set **Home URL** to the PC LAN IP for when you are on home Wi‑Fi.
4. **Bearer token:** unchanged — same `MIMIR_CLIENT_TOKEN` as at home.

Cleartext HTTP is intentional — WireGuard encrypts traffic inside the tailnet.
Push-to-talk (`/v1/stt`, `/v1/tts`) uses the same resolved URL.

**Troubleshooting away from home:**

| Symptom | Check |
|---|---|
| Browser `/health` OK but app "Unable to resolve host" | Use **100.x IP**; or Android Settings → Private DNS → **Off** |
| Brain unreachable | Tailscale enabled on phone; correct URL; PC on and brain running |
| Works at home, not away | Windows firewall missing Tailscale rule — run `scripts/install_brain_firewall.ps1 -Tailscale` (Admin PowerShell) on PC |
| 401 on chat | Token matches PC `.env` |
| 429 on chat | Too many wrong tokens — wait 60 s or use correct token |

### Smoke test (phone browser)

Open `http://<PC-LAN-IP>:8000/health` — expect JSON with `"status": "ok"`.

## Usage

### Typed chat

- Type a message and tap **Send**
- **+** (top bar): start a **new chat** — fresh thread, no prior context (like TUI `/new`)
- **History** icon: switch to a past conversation
- **Settings** icon: change URL / token
- **Write flows:** when Mimir asks to confirm a change, tap **Confirm** or **Cancel**
  (sends `yes`/`ja` or `no`/`nee`)

### Push-to-talk (T-025)

- **Hold** the mic button (bottom-left), speak, **release** to send
- Status line shows: Recording → Transcribing → Working → Speaking
- Your transcript appears in the chat thread as a user bubble before the assistant reply
- **Barge-in:** hold mic again while Mimir is speaking to stop playback and start a new utterance
- **Spoken confirm:** when a write-confirm bubble is showing, say *ja* / *yes* (or *nee* / *no* to cancel) on PTT instead of tapping buttons
- First use prompts for **RECORD_AUDIO** permission

### Shared history with TUI

The mobile app persists `conversation_id` locally. The TUI starts a fresh thread each
launch; to see the same history on PC, use TUI `/history` and pick the conversation
(most recent = top of list).

## Troubleshooting

| Symptom | Check |
|---|---|
| Mic button does nothing | Grant microphone permission in Android settings |
| “Speech recognition failed” | Brain STT (Whisper) running on PC; check brain logs |
| “Speech synthesis failed” | Brain TTS (Piper) running; check `/v1/tts` |
| Garbled transcript | Speak clearly; hold button entire utterance; check 16 kHz WAV path |
| Brain unreachable | Same as typed chat — LAN URL, token, firewall |
| No audio during reply | Phone volume; TTS WAV returned (not empty) |

## Acceptance checklist (operator)

**T-024 typed chat** — run on **Archer** home Wi‑Fi with brain running:

1. [x] Debug APK installs on operator Android phone
2. [x] Settings: save LAN URL + token → health indicator green
3. [x] Read: *“What's on the shopping list?”* → correct answer
4. [x] Write: *“Add coffee to the shopping list”* → Mimir asks → **Confirm** → read-back OK
5. [x] Open TUI on PC → `/history` → same conversation → history matches
6. [x] Brief airplane mode → error shown → Wi‑Fi back → chat recovers

**T-025 push-to-talk** — after T-024 passes:

1. [x] Hold mic → NL or EN question → spoken reply matches typed quality
2. [x] Write via voice → confirm prompt → spoken *ja*/*yes* OR button → write succeeds
3. [x] Barge-in: interrupt TTS with new PTT hold
4. [x] Typed chat still works; TUI unchanged

**T-015 remote access (M7)** — operator accepted 2026-09-01:

1. [x] Mobile data + Tailscale → chat + PTT over away URL
2. [x] Public IP / tailnet URL with Tailscale off → unreachable
3. [x] Dual URL: Archer Wi‑Fi + home URL; away via Tailscale fallback
4. [x] Home URL on Archer Wi‑Fi with Tailscale off

## Architecture

| Component | Role |
|---|---|
| `BrainApi` | OkHttp — health, chat SSE, STT, TTS |
| `SseParser` | SSE `data:` line parser |
| `AudioRecorder` / `WavEncoder` | 16 kHz mono PCM → WAV for STT |
| `AudioPlayer` | WAV playback for TTS |
| `SettingsRepository` | DataStore (home/away URLs, conversation id) + EncryptedSharedPreferences (token) |
| `BrainUrlResolver` | LAN-first probe, Tailscale away fallback (T-015) |
| `ConfirmationDetector` | M3 write-confirm + spoken ja/yes detection |
| `ChatViewModel` | Typed + voice pipelines, shared `completeChatTurn` |
| `PttMicButton` | Hold-to-talk gesture |

## Out of scope

- Always-on wake word, Home Assistant / Wyoming
- iOS, offline mode, per-device tokens
- Certificate pinning
- Streaming STT partial results
