# Discord is a brain tool, not a Chat client

Discord could have been another thin front door (like the TUI) or a peer bot the brain calls indirectly. We decided the brain owns a **send-message tool** using a bot token and a **Channel allowlist** (snowflake IDs in config/env). Discord is not a Chat client; Telegram/Matrix-style “bot as product UI” stays out of scope. Expand to read/DM only after send-only proves useful.

**Considered options:** Discord bot as HTTP Chat client; brain → custom bot API → Discord; Matrix/Telegram instead.
