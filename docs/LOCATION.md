# Location Skill

`message.location` is resolved to city/country with Nominatim and to an IANA
timezone locally with `timezonefinder`. Network geocoding failures are isolated;
timezone resolution and Telegram polling remain available.

The candidate is held only in process until the same Telegram user presses
Save. The callback contains no coordinates. A decline discards it. The separate
`LOCATION_DB_PATH` database keeps one row per `(user_id, location_type)`; only
`current` is implemented, while `home` and `work` fit the schema. Upsert replaces
the last value, so movement history is not retained.

`LocationService.context(owner_id)` supplies city and timezone to the agent's
instructions without creating a Memory fact or changing Conversation storage.
`reminder_location()` is the future read-only integration point; proximity
triggers are not implemented.

The `get_user_location` tool receives `trusted_owner_id` from Jarvis, never from
model arguments. Health returns only status and the aggregate active-user count.
