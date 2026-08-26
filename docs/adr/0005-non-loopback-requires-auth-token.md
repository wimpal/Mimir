# Non-loopback bind requires Auth token

Phase 7 opens the brain to other devices on the local network. Leaving
`auth.mode: none` on a non-loopback bind would make chat reachable without a
shared secret. We decided: if `runtime.host` is not loopback (`127.0.0.1`,
`::1`, `localhost`), the brain **refuses to start** unless `auth.mode: token`
and a non-empty Auth token (`MIMIR_AUTH_TOKEN`) are set. Default remains
loopback + `auth.mode: none` for same-machine TUI friction. We rejected
auto-enabling token mode (hides misconfig) and warn-only (lets an open LAN bind
ship by mistake).

**Considered options:** auto-enable token on non-loopback; soft docs / warn only;
always require token even on localhost.
