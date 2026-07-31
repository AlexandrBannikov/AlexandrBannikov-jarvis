# Conversation state

Jarvis keeps a bounded, short-lived conversation history separate from long-term memory. It is scoped by Telegram `owner_id + chat_id + thread_id`, persists in `CONVERSATION_DB_PATH`, and expires after `CONVERSATION_STATE_TTL_MINUTES` (default 60 minutes). The sliding window is limited by `CONVERSATION_HISTORY_MAX_MESSAGES`.

The state records the active goal, a pending clarification question, and compact collected facts. Short answers such as “1.4 л, 150 л.с.” are interpreted against a live pending question. Explicit cancellation or a clear new topic closes that state. Expiration never deletes long-term memory.

Recent messages and pending state always outrank project memory. A stored project is never selected as the current topic merely because it is the first or only project available. If no recent context exists, Jarvis asks for a neutral reminder instead of guessing.

Conversation state contains no secrets, private attachments, or unrestricted tool output. Storage failures degrade to ordinary answering and do not stop Telegram polling. `/conversation` shows a compact owner-scoped summary; `/reset_context` clears only the current short-term state.
