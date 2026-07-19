# StackChan product repository

## Product invariants

- The Mac is the source of truth for identity, memory, policy, secrets, and task state.
- Never persist API keys, face embeddings, or conversation history on the ESP32 robot.
- All configuration changes are versioned and reversible.
- Memories are isolated by `user_id`; inferred memories for children require review.
- Device actions that can affect safety, privacy, purchases, locks, or accounts require adult approval.
- Chinese and English are first-class product languages.

## Development

- Keep the real-time voice path separate from the management/control path.
- Add tests for user isolation, configuration rollback, and authorization when changing these areas.
- Put generated runtime state under `var/`; it must remain untracked.
- For implementation work, report a unique Codex task through `robotctl tasks report` when the local control API is reachable. Update it at meaningful progress points and on completion; an offline robot service must never block engineering work.
