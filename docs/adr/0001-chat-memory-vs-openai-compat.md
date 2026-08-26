# Chat memory on `/v1/chat` only; OpenAI-compat stays client-history

Phase 4 needs SQLite Conversations/Messages, but wiring the same persistence into `/v1/chat/completions` would enlarge the phase and couple HA’s message list to brain-owned history before voice needs it. We decided: Mimir `/v1/chat` owns Conversation identity (`message` + optional `conversation_id`, SQLite History window); OpenAI-compat remains a pass-through of the client `messages` array with no SQLite read/write in Phase 4. Revisit when Assist/HA must share durable threads with the chat client.

**Considered options:** one memory model on both endpoints now; defer all persistence until a single HA-ready path exists.
