# CI: real MySQL for DB-backed tests + asyncio-mode fix

**Repo**: `udab-server`. Two small, separate PRs — deliberately NOT part of the ZoomInfo-exit PR (#654), whose scope stays as-is.

## Background (diagnosis from CI run 29418135116, PR #654, 2026-07-15)

PR #654 introduced the repo's first true DB-integration tests (`tests/test_zoominfo_exit_matcher.py`, most of `tests/test_zoominfo_exit_engine.py`); every pre-existing test mocks the DB layer. Two independent CI gaps surfaced:

1. **CI has no database.** `.github/workflows/pr.yaml` → "Run tests" runs pytest in a bare `docker run` with `DB_CONNECTION_STRING="mysql+pymysql://test:test@localhost/test_db"` — nothing listens on 3306 anywhere in the job. Result: 7 `ERROR at setup` (`Can't connect to MySQL server on 'localhost'`) — all of `test_zoominfo_exit_matcher.py`.
2. **Async tests are silently skipped in CI.** The `Dockerfile` copies `./app`, `./alembic.ini`, `./migrations`, `./tests`, `./run_remote.sh`, `./migrate.sh` — but never `pytest.ini` (which sets `asyncio_mode = auto`). Inside the image pytest-asyncio runs in strict mode, so **every unmarked `async def` test is skipped with only a warning** (`PytestUnhandledCoroutineWarning`), not run, not failed. Today that's the 32 async tests in `test_zoominfo_exit_engine.py`; the pattern will silently swallow any future async test whose author forgets a `pytestmark`. (`test_zoominfo_exit_matcher.py` only runs in CI because it defensively carries `pytestmark = pytest.mark.asyncio`.)

The same missing-`pytest.ini` gap is why local `docker compose exec fastapi pytest` needs `-o asyncio_mode=auto` passed by hand.

## Constraints (Tomas, 2026-07-15)

- **No seed data in CI — ever.** There are no seeds to speak of and we are not inventing any. A CI database is acceptable **if and only if** it is a fresh, empty instance carrying nothing but the migrated schema (`alembic upgrade head`). Tests must create and clean up 100% of their own fixtures — which is exactly how the zoominfo-exit tests are written (insert via `AsyncSessionLocal`, prefix-scoped cleanup).
- Both fixes are separate PRs, not additions to #654.

## Sequencing

**PR-1 (database) must land before PR-2 (asyncio mode).** PR-2 un-skips the 32 async engine tests in CI; most of them are DB-backed, so applying PR-2 first would convert silent skips into 32 connection errors. With PR-1 landed, PR-2 turns them into genuinely passing tests.

Interim for #654: after PR-1 merges, re-run the #654 checks (or rebase); no change to #654 itself is needed. Do NOT add skip-when-no-DB guards to the test files — that would institutionalize the silent-skip problem we're removing.

---

## PR-1 — fresh schema-only MySQL in the CI test job

**File**: `.github/workflows/pr.yaml`, `test` job only. The `deploy` job (ECR) is untouched.

1. Add a MySQL 8.0 sidecar the test container can reach. The "Run tests" step already uses `docker run`, so the least-magic approach is a GitHub Actions `services:` block on the `test` job (services publish ports on the runner host) plus `--network host` on the existing `docker run`, so the container's `localhost:3306` resolves to the service:

   ```yaml
   services:
     mysql:
       image: mysql:8.0
       env:
         MYSQL_ROOT_PASSWORD: test
         MYSQL_DATABASE: test_db
         MYSQL_USER: test
         MYSQL_PASSWORD: test
       ports:
         - 3306:3306
       options: >-
         --health-cmd "mysqladmin ping -h localhost -ptest"
         --health-interval 5s --health-timeout 5s --health-retries 20
   ```

   (Equivalent alternative if `--network host` is undesirable: `docker network create`, run a `mysql:8.0` sidecar on it, and `--network <net>` both containers, pointing `DB_CONNECTION_STRING` at the sidecar's alias. Implementer's choice; host networking is less YAML.)

2. **Migrate before testing — this is the "schema-only" guarantee.** Change the "Run tests" step to run `alembic upgrade head` first, e.g.

   ```
   docker run --rm --network host \
     -e DB_CONNECTION_STRING=... -e READER_DB_CONNECTION_STRING=... -e ALLOWED_CORS_ORIGIN="*" \
     udab-server-test sh -c "alembic upgrade head && python -m pytest tests/ -v --tb=short"
   ```

   The service container is created fresh per job run and discarded after — no state ever persists between runs, no seeds are loaded, the DB contains exactly what the migration chain creates. (The `9d4e1c7a2f38` index migration is instant on an empty `5x5_universal_person`.)

3. The migration-heads check step stays as-is (it needs no DB).

**Acceptance**
- [ ] `test_zoominfo_exit_matcher.py` passes in CI (7 tests, currently erroring).
- [ ] Full suite result otherwise unchanged from run 29418135116 (3198 passed / 190 skipped baseline).
- [ ] Job wall-time increase is acceptable (~1–2 min for MySQL startup + full migration chain on an empty DB) — report the actual delta in the PR.
- [ ] No fixture/seed files added anywhere.

**Risks / notes**
- `alembic upgrade head` in CI now exercises every migration on every PR — that's a feature (it would have caught a broken migration chain at the SQL level, not just the heads count), but it means a PR with a long-running-on-empty-DB migration will slow CI. Acceptable.
- MySQL 8.0 image matches the docker-compose dev DB (`mysql:8.0`) — keep them in lockstep if either is ever bumped.

---

## PR-2 — make asyncio mode consistent everywhere (after PR-1)

**Files**: `Dockerfile`, optionally `.github/workflows/pr.yaml`, `tests/test_zoominfo_exit_matcher.py`.

1. **Root fix**: `COPY ./pytest.ini /` in the Dockerfile (next to `alembic.ini`; pytest's rootdir discovery walks up from `tests/`, and the CI invocation runs from `/`, so `/pytest.ini` is picked up). This makes the image honor `asyncio_mode = auto` exactly like the repo checkout does.
2. Belt-and-braces (optional but cheap): keep `-o asyncio_mode=auto` OFF the CI command line — we *want* CI to prove the image config is right, not mask it. Instead add a CI guard that fails on silent skips: append `-W error::pytest.PytestUnhandledCoroutineWarning` (or `filterwarnings = error::...` scoped in pytest.ini) so an unmarked async test can never again skip silently. Implementer judgment on the exact mechanism; the requirement is: **an async test that doesn't run must fail the build, not warn.**
3. Cleanup, same PR: remove the now-redundant `pytestmark = pytest.mark.asyncio` workaround (and its explanatory comment) from `tests/test_zoominfo_exit_matcher.py` — with auto mode in the image it's dead weight; keeping it invites cargo-culting into future files.
4. Local ergonomics follow for free: `docker compose exec fastapi pytest tests/...` stops needing `-o asyncio_mode=auto` once the dev image is rebuilt (document in the PR description).

**Acceptance**
- [ ] CI runs the 32 previously-skipped async tests in `test_zoominfo_exit_engine.py` and they pass (requires PR-1's DB).
- [ ] `PytestUnhandledCoroutineWarning` count in the CI log is zero, and a deliberately-unmarked async test fails the build rather than skipping (verify once locally, don't commit the probe).
- [ ] Total passed count rises by exactly the un-skipped tests; no other deltas.
