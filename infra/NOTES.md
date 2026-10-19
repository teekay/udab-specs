---
kind: notes
status: done
area: infra
updated: 2026-10-19
repos: [udab-server]
summary: "Living reference: reader vs writer sessions in udab-server, how tests fake the auth middleware, the writer-GET guard."
---

# Infra — how it works today

## Reader vs writer sessions (since 2026-10-19)

Spec: [read-replica-rollout.md](read-replica-rollout.md).

- `app/db.py`: `get_async_db` / `get_db` are the writer, `get_async_readonly_db` /
  `get_readonly_db` the Aurora reader. Both reader engines are 20 + 30, recycle 1800,
  and the async reader sets the writer's session `sql_mode`.
- **Every GET must use a reader dependency** unless it is in `WRITER_GETS` in
  `tests/test_reader_routes.py` with a reason; the guard test fails otherwise. Rules
  for staying on the writer: the handler (or anything it hands the session to)
  writes, or the client re-fetches that exact GET right after a mutation.
- `jwt_middleware` (`app/main.py`) resolves the JWT user and the `/api/` token on
  `ReadonlyAsyncSessionLocal`. Nothing per-request touches the writer any more.
- A read that follows a write in the same user flow stays on the writer
  (`store.history`, the four detail GETs the client re-fetches after a PATCH).
- Long chunked reads on the reader: `commit()` between chunks (a read view held
  open on a reader pins undo purge on the writer).

## Testing gotchas

- Tests that fake the middleware's user patch `app.db.ReadonlyAsyncSessionLocal`
  (not `AsyncSessionLocal`). That patch also feeds any route-level
  `get_async_readonly_db` that is not overridden, so a route test must override
  `get_async_readonly_db` (and `get_readonly_db` for sync) itself; the autouse
  fixture in `tests/conftest.py` overrides both async dependencies with the same
  dummy session.
- Modules that bind a session factory at import (`strategy_activity`,
  `ai_assistant.store`, `data_export`) must be patched on the module, not on `app.db`.
- `scripts/test.sh` points the reader DSN at the same test DB. Some files insist
  the DB is named `udab_test`; run the suite with `TEST_DB=udab_test`.
