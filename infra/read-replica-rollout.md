---
kind: spec
status: in-progress
area: infra
updated: 2026-10-19
repos: [udab-server]
summary: "Move all read traffic off the Aurora writer: auth middleware first, Hub sibling reads, then ~160 clean read routes."
---

# Read replica rollout — move read traffic off the writer

Status: IN-PROGRESS (2026-10-19) — built on branch
`utilize-readonly-replica-more` in udab-server, five commits (one per
tier, 2637f85 → 722f923 → 4530d8c → acbc5b4 → d1a0bd9), full suite green
(6072 passed on `udab_test`); not yet merged. Analysis was done on
master at `709fc7f`.

## Implemented (2026-10-19)

Everything below the line shipped as specified, with these deltas:

- `store.history` (AI assistant) stays on the writer: it loads the
  previous turn right after that turn is written, so a reader read
  inside replica lag would drop it from the context. `conversations`
  moved.
- `GET /sequences/datatable` was left alone: its router is not mounted
  and the handler returns stub data without a session.
- Dropping the unused writer session from the extension's
  `validate_token` also means `get-metro-areas`, `get-toc-reasons`,
  `get-products`, `get-users`, `get-county-zip-codes` open no session
  at all now (they only ever reached the DB through that parameter).
- `tests/test_reader_routes.py` is the record: `READER_ROUTES` (async),
  `SYNC_READER_ROUTES`, and `WRITER_GETS` (the allowlist the guard test
  enforces). Add a route there when it moves.
- Four routes are skipped in the live-call test for pre-existing bugs,
  wiring still asserted: `/company/countries|states|metros` are
  shadowed by `/company/{id}` (registered first) and
  `/clients/{client_id}` always 500s (`ClientGet` requires `tags`,
  which `Client` lacks). Both are worth their own fix.

## Why

The Account Management Hub is about to get many more users. Its queries
are the heaviest in the app and the Hub list/filter/detail routes already
run on the reader (`get_async_readonly_db`, see
[appointment-emails/hub-performance.md](../appointment-emails/hub-performance.md)).
But every request still touches the writer, and most other read routes
still run on it. The writer must stay fast for the sync jobs and the
extension's per-dial writes, so everything that only reads moves to the
reader.

Scan method: AST inventory of all 420 mounted routes (method, path, DB
dependency, write statements in the handler), then a name-based
reachability pass from each handler into `app/services` for session
`add/flush/commit/delete`, `insert()/update()/delete()` constructs and
raw `INSERT/UPDATE/DELETE` SQL. Every remaining flag was checked by hand;
the ones listed under "Must stay" are the only real writes on a read
route. (`db.query(...)` collides with the Redshift service's `query()`
in a name-based pass; that is a false positive and was ignored.)

Already on the reader (35 routes, nothing to do): `appointment_calls.py`
GETs, `data_export.py` option/list GETs, `filter_options.py`,
`demo_reporting.py`, `aiq_primary.py`, `five_five_universal_person.py`
option routes, `prospects.py`/`company.py`/`sf_company.py`/`sf_contact.py`
option routes, `notifications/stream`.

## Tier 0 — the auth middleware (do first, biggest win)

`app/main.py` `jwt_middleware` opens `AsyncSessionLocal()` (writer) on
**every** authenticated request to load `User` + role (`:163`), and the
`/api/` branch does the same `ApiToken` lookup (`:163`, api-key branch).
A Hub page load is 4–6 requests; each one takes a writer connection
before the handler runs, so the routes already on the reader still cost
the writer one round trip each.

Change: both lookups use `ReadonlyAsyncSessionLocal()`. Route-level
`get_current_user` (`app/utils/auth.py`) is a pure JWT decode and needs
nothing.

Staleness: a user created or re-permissioned inside replica lag gets
"User not found" / "User data mismatch" once and re-logs in; the
middleware already forces that on any permission change, so behaviour
is unchanged. Nothing else reads the user row per request.

## Tier 1 — Hub users' sibling reads still on the writer

The Hub page itself is on the reader (list, filters, detail, transcript,
building image, download, export). These are called by the same users
from the Hub and the pages beside it in the sidebar:

| route | handler | note |
|---|---|---|
| `GET /appointment-emails` | `app/routes/appointment_email.py:220` | count + page + two lookups + recipient resolution; heaviest unmoved read |
| `GET /appointment-emails/accounts` | `:348` | group-by scan |
| `GET /appointment-emails/{id}` | `:487` | flyout; the `.add(` is `user_ids.add`, a set |
| `GET /appointment-emails/{id}/transcript` | `:476` | |
| `GET /appointment-emails/{id}/preview` | `:585` | renders from DB rows only |
| `GET /view-preferences/{page}` | `app/routes/view_preference.py:34` | tiny, but on every Hub load |
| `GET /call-transcripts` + `/{id}/download` + `/{id}/audio` | `app/routes/call_transcript_local.py:36,121,156` | Calls page |
| `POST /appointment-calls/exports` | `app/routes/appointment_calls.py:139` | only reads before `launch_job`; optional |

Read-after-write checked in `udab-client`: the Hub applies the
salesmanship PUT response to the row (`AppointmentCallSalesmanship.vue`
emits `saved` with the response), the Appointments flyout emits mutation
responses into the list (`applyDetailToRow`), and `putViewPreferences`
is debounced with no GET after it. No Hub flow re-fetches from the
server right after a write, so replica lag cannot surface there.

## Tier 2 — async read routes, drop-in swap

`Depends(get_async_db)` → `Depends(get_async_readonly_db)`. No reachable
write in any of these (set `.add` flags verified by hand: `exclusivity`,
`talk_track_inheritance`, `talk_track_render`, `report_service`,
`csv_field_mapper`, `company.get_metros`). Grouped roughly by value:
adherence/engage reports, extension stats, strategy activity and removal
reports, transcription jobs and the `/api/` transcript routes, then the
admin lists.

| route | handler |
|---|---|
| `GET /account/{sf_id}/exclusivity-detail` | `account.py:82` |
| `GET /api-tokens` | `api_token.py:29` |
| `GET /call-adherence/summary` | `call_adherence.py:34` |
| `GET /call-adherence/nodes` | `call_adherence.py:40` |
| `GET /call-adherence/calls` | `call_adherence.py:50` |
| `GET /call-adherence/filter-options` | `call_adherence.py:89` |
| `GET /call-adherence/calls/{adherence_id}` | `call_adherence.py:111` |
| `GET /call-adherence-engage/summary` | `call_adherence_engage.py:23` |
| `GET /call-adherence-engage/beats` | `call_adherence_engage.py:29` |
| `GET /call-adherence-engage/insights` | `call_adherence_engage.py:35` |
| `GET /call-adherence-engage/calls` | `call_adherence_engage.py:45` |
| `GET /call-adherence-engage/filter-options` | `call_adherence_engage.py:90` |
| `GET /call-adherence-engage/calls/{adherence_id}` | `call_adherence_engage.py:101` |
| `GET /clients/datatable` | `client.py:27` |
| `GET /clients/export` | `client.py:61` |
| `GET /clients/{client_id}` | `client.py:143` |
| `GET /clients` | `client.py:188` |
| `GET /company/industries` | `company.py:233` |
| `GET /company/countries` | `company.py:293` |
| `GET /company/states` | `company.py:313` |
| `GET /company/metros` | `company.py:333` |
| `GET /contact-prospects/stats/{strategy_id}` | `contact_prospects.py:18` |
| `GET /dnc/datatable` | `dnc.py:74` |
| `GET /dnc/export/{account_id}` | `dnc.py:106` |
| `GET /email-validation` | `email_validation.py:130` |
| `GET /email-validation/{validation_id}/download/{kind}` | `email_validation.py:187` |
| `GET /extension/stats/summary` | `extension_stat.py:23` |
| `GET /extension/stats/detail` | `extension_stat.py:135` |
| `GET /extension/stats/export` | `extension_stat.py:226` |
| `GET /extension/stats/object-updates` | `extension_stat.py:253` |
| `GET /extension/version` | `extension_version.py:21` |
| `GET /heyreach-instances` | `heyreach.py:28` |
| `GET /heyreach-campaigns/datatable` | `heyreach.py:114` |
| `GET /heyreach-campaigns/` | `heyreach.py:201` |
| `GET /heyreach-campaigns/{id}` | `heyreach.py:215` |
| `GET /landing-pages` | `landing_page.py:21` |
| `GET /landing-pages/unassigned` | `landing_page.py:58` |
| `GET /api/landing-pages/domains` | `landing_page_hosting.py:51` |
| `GET /api/landing-pages/domains/{domain}/dns` | `landing_page_hosting.py:77` |
| `GET /api/landing-pages/operations/{operation_id}` | `landing_page_hosting.py:136` |
| `GET /leadmagic/credits` | `leadmagic.py:51` |
| `GET /leadmagic/settings` | `leadmagic.py:88` |
| `GET /mailboxes` | `mailbox.py:21` |
| `GET /notifications/{user_id}` | `notification.py:114` |
| `GET /prospects/job-functions` | `prospects.py:293` |
| `GET /roles` | `role.py:20` |
| `GET /roles/{role_id}` | `role.py:67` |
| `GET /search-activities/search-by-user` | `search_activity.py:70` |
| `GET /search-export/{export_id}/download` | `search_export.py:105` |
| `GET /smartlead-campaigns/unassigned` | `smartlead_campaign.py:27` |
| `GET /smartlead-campaigns/all` | `smartlead_campaign.py:56` |
| `GET /smartlead-campaigns/{id}` | `smartlead_campaign.py:72` |
| `GET /smartlead-export/{export_id}/uploaded-records` | `smartlead_export.py:150` |
| `GET /strategies/{strategy_id}/availability-job-status` | `strategy.py:967` |
| `GET /strategy-activity/report` | `strategy_activity.py:67` |
| `GET /strategy-activity/report/trends` | `strategy_activity.py:254` |
| `GET /strategy-activity/record-types` | `strategy_activity.py:306` |
| `GET /strategy/{strategy_id}/activities` | `strategy_activity.py:342` |
| `GET /strategy-activity/{activity_id}/logs` | `strategy_activity.py:365` |
| `GET /strategy-activity/{activity_id}/contacts` | `strategy_activity.py:389` |
| `GET /strategy-activity/{activity_id}/contacts/export` | `strategy_activity.py:520` |
| `GET /strategy-activity/{activity_id}/interactive-prospects` | `strategy_activity.py:711` |
| `GET /strategy-activity/{activity_id}` | `strategy_activity.py:964` |
| `GET /strategy-activity/{activity_id}/prospects` | `strategy_activity.py:984` |
| `GET /strategy-activity/{activity_id}/prospects/export` | `strategy_activity.py:1175` |
| `GET /strategies/{strategy_id}/removals` | `strategy_removal.py:129` |
| `GET /strategy-removal/{removal_id}` | `strategy_removal.py:170` |
| `GET /strategy-removal/{removal_id}/prospects` | `strategy_removal.py:200` |
| `GET /strategy-removal/{removal_id}/status` | `strategy_removal.py:359` |
| `GET /strategy-removal/{removal_id}/logs` | `strategy_removal.py:378` |
| `GET /strategies/{strategy_id}/available-removal-criteria` | `strategy_removal.py:404` |
| `GET /survey/report` | `survey_report.py:29` |
| `GET /survey/report/data` | `survey_report.py:102` |
| `GET /tags` | `tag.py:20` |
| `GET /talk-track/survey-url` | `talk_track.py:420` |
| `GET /talk-track/show/{talk_track_id}` | `talk_track.py:604` |
| `GET /talk-track/data/{talk_track_id}` | `talk_track.py:700` |
| `GET /talk-track/search` | `talk_track.py:742` |
| `GET /talk-track/details/{talk_track_id}` | `talk_track.py:776` |
| `GET /talk-track/sessions` | `talk_track.py:797` |
| `GET /talk-track/reports/accounts-missing-talk-track` | `talk_track.py:840` |
| `GET /talk-track/session/{session_id}/actions` | `talk_track.py:1076` |
| `GET /talk-track/{talk_track_id}/export` | `talk_track.py:1637` |
| `GET /transcription-jobs` | `transcription_job.py:92` |
| `GET /transcription-jobs/export/runs.csv` | `transcription_job.py:267` |
| `GET /transcription-jobs/export/tasks.csv` | `transcription_job.py:314` |
| `GET /transcription-jobs/{job_id}/tasks` | `transcription_job.py:363` |
| `GET /data-source/{data_source_id}/default-mapping/{entity_type}` | `data_ingestion/mapping.py:362` |
| `GET /data-sources` | `data_ingestion/metadata.py:26` |
| `GET /upload/{upload_id}/validation-summary` | `data_ingestion/processing.py:27` |
| `GET /search` | `data_ingestion/uploads.py:197` |
| `GET /upload/{upload_id}` | `data_ingestion/uploads.py:245` |
| `GET /upload/{upload_id}/records` | `data_ingestion/uploads.py:294` |
| `GET /upload/{upload_id}/imported-records` | `data_ingestion/uploads.py:426` |
| `GET /upload/{upload_id}/companies` | `data_ingestion/uploads.py:603` |
| `GET /api/replydesk/campaigns/{smartlead_campaign_id}` | `api/replydesk_api.py:33` |
| `GET /api/transcripts` | `api/transcript_search_api.py:93` |
| `GET /api/sf-tasks/{sf_task_id}/transcript` | `api/transcription_job_api.py:137` |
| `GET /api/sf-tasks/{sf_task_id}/adherence` | `api/transcription_job_api.py:192` |
| `GET /api/transcription-jobs/{job_id}` | `api/transcription_job_api.py:233` |
Hand-opened writer sessions on pure reads, same tier:

- `app/routes/strategy_activity.py:577` `_export_contacts_from_sf_contact_prospect` → `ReadonlyAsyncSessionLocal`.
- `app/services/ai_assistant/store.py:203` (`history`) and `:316` (`conversations`) → `ReadonlyAsyncSessionLocal`; the rest of the store writes and stays.
- `app/routes/data_export.py:51` `_is_opensearch_enabled` opens a sync `SessionLocal()` per request for a feature flag → `ReadonlySessionLocal`.

## Tier 3 — sync (`get_db`) read routes

Same swap with `get_readonly_db`. These include the Prospects search page
(`/prospects/search`, `/company/search`, `/sf-company/search`,
`/sf-contact/search`, `/five-five/universal-person/search`) which is the
second-heaviest read surface after the Hub.

**Before moving any of these, size the sync reader engine.**
`readonly_engine` in `app/db.py:16` is created with SQLAlchemy defaults
(pool 5, overflow 10); the async reader has 20 + 30. Give it at least
the writer's sync settings, or convert the hot ones (the five searches)
to async sessions while touching them.

| route | handler |
|---|---|
| `POST /company/search` | `company.py:33` |
| `GET /company/{id}` | `company.py:279` |
| `POST /five-five/universal-person/search` | `five_five_universal_person.py:49` |
| `GET /five-five/universal-person/{up_id}` | `five_five_universal_person.py:480` |
| `GET /pixel-accounts` | `pixel_account.py:50` |
| `POST /prospects/search` | `prospects.py:35` |
| `GET /prospects/{id}` | `prospects.py:320` |
| `GET /sequences/datatable` | `sequence.py:28` |
| `GET /settings` | `setting.py:19` |
| `GET /sf-accounts` | `sf_account.py:50` |
| `GET /sf-accounts/datatable` | `sf_account.py:87` |
| `GET /sf-accounts/export` | `sf_account.py:144` |
| `GET /sf-accounts/{sf_id}` | `sf_account.py:216` |
| `POST /sf-company/search` | `sf_company.py:33` |
| `GET /sf-company/{id}` | `sf_company.py:236` |
| `POST /sf-contact/search` | `sf_contact.py:31` |
| `GET /sf-contact/{id}` | `sf_contact.py:337` |
| `GET /sf-metro/areas` | `sf_metro.py:15` |
| `GET /sf-metro/counties` | `sf_metro.py:20` |
| `GET /sf-projects/{sf_id}` | `sf_project.py:15` |
| `GET /sf-projects/` | `sf_project.py:29` |
| `GET /sf-users/search` | `sf_user.py:18` |
| `GET /sf-users/{user_id}` | `sf_user.py:48` |
| `GET /smartlead-instances` | `smartlead_instance.py:23` |
| `GET /smartpixl/dealers` | `smartpixl.py:204` |
| `GET /smartpixl/dealers/{dealer_id}` | `smartpixl.py:221` |
| `GET /smartpixl/dealers/{dealer_id}/leads` | `smartpixl.py:336` |
| `GET /smartpixl/leads` | `smartpixl.py:362` |
| `GET /smartpixl/leads/export` | `smartpixl.py:402` |
| `GET /smartpixl/facebook-stats` | `smartpixl.py:465` |
| `GET /smartpixl/report` | `smartpixl.py:560` |
| `GET /smartpixl/dealers/{dealer_id}/templates` | `smartpixl.py:617` |
| `GET /smartpixl/dealers/{dealer_id}/templates/{template_id}/preview` | `smartpixl.py:766` |
| `GET /smartpixl/email-stats` | `smartpixl.py:779` |
| `GET /smartpixl/email-sends` | `smartpixl.py:958` |
| `GET /smartpixl/email-sends/export` | `smartpixl.py:1009` |
| `GET /smartpixl/records` | `smartpixl.py:1289` |
| `GET /smartpixl/records/export` | `smartpixl.py:1308` |
| `GET /smartpixl/contacts` | `smartpixl.py:1345` |
| `GET /smartpixl/unsubscribes` | `smartpixl.py:1420` |
| `GET /strategies/account/{account_id}` | `strategy.py:550` |
| `GET /strategies/datatable` | `strategy.py:625` |
| `GET /strategies/datatable/ids` | `strategy.py:808` |
| `GET /strategies/{strategy_id}` | `strategy.py:897` |
| `GET /strategies/{strategy_id}/poll-activities` | `strategy.py:1089` |
| `POST /strategies/bulk-create/parse` | `strategy_bulk_create.py:50` |
| `GET /strategies/{strategy_id}/data` | `strategy_data.py:339` |
| `POST /strategies/bulk-edit/preview` | `strategy_data.py:412` |
| `GET /strategies/{strategy_id}/history` | `strategy_history.py:19` |
| `GET /strategies/{strategy_id}/notes` | `strategy_note.py:20` |
| `GET /users/datatable` | `user.py:26` |
| `GET /users/{user_id}` | `user.py:114` |
| `GET /user-columns` | `user_column.py:19` |
| `GET /visitor-resolution-pixel/domains` | `visitor_resolution_pixel.py:162` |
| `GET /visitor-resolution-pixel/summary` | `visitor_resolution_pixel.py:178` |
| `GET /visitor-resolution-pixel/records` | `visitor_resolution_pixel.py:226` |
| `GET /visitor-resolution-pixel/records/export` | `visitor_resolution_pixel.py:319` |
| `GET /visitor-resolution-pixel/settings` | `visitor_resolution_pixel.py:370` |
Read-after-write rule for this tier: the Strategies and Users screens
re-fetch the detail GET right after a PATCH. Aurora lag is normally tens
of milliseconds, under a browser round trip, but not guaranteed. For
`GET /strategies/{id}`, `/strategies/{id}/data`, `/users/{id}` and
`/sf-accounts/{sf_id}` either keep the detail GET on the writer or make
the client apply the PATCH response (as the Hub does). Lists and reports
move regardless.

## Tier 4 — extension reads (medium)

`validate_token` (`app/routes/extension.py:144`) takes a writer session it
never uses; drop that parameter. Then these three move:

| route | handler |
|---|---|
| `GET /aiq-extension/version` | `extension.py:239` |
| `GET /aiq-extension/last-refresh` | `extension.py:910` |
| `GET /aiq-extension/call-settings` | `extension.py:1193` |
Every other `/aiq-extension` route calls `log_extension_activity`
(`extension.py:182`: add, flush, upsert, commit) on each request,
including `get-prospects`. That path is write-bound by design; nothing
to gain there.

## Must stay on the writer

| route | handler | why |
|---|---|---|
| `GET /account/{sf_id}` | `app/routes/account.py:19` | get-or-create of the `Account` row on first read |
| `GET /survey/`, `GET /survey/reload` | `app/routes/survey.py:110,314` | `survey_sync.sync_and_get_*` commit |
| `GET /talk-track/embed` | `app/routes/talk_track.py:438` | `sync_and_get_contact` commit |
| `GET /rt` | `app/routes/resolve_identity.py:22` | tracking pixel, writes per hit |
| `GET /leadmagic/status` | `app/routes/leadmagic.py:27` | `_budget_row` insert |
| `GET /landing-pages/sync` | `app/routes/landing_page.py:78` | runs the Unbounce sync |
| all `/aiq-extension` but the three above | `app/routes/extension.py` | activity log on every call |

Not DB-bound, nothing to do: Ignite APIs (OpenSearch), `data-export`
`/options/*/opensearch`, `sf-account` typeahead/industries, the
ganalytics/gsc/gtmetrix/semrush routes (Redshift), `documentation`,
`appointment-calls/exports/{id}` (S3), `model-fields`.

## Rollout notes

- **Engine parity.** `readonly_async_engine` (`app/db.py:22`) lacks the
  writer's `connect_args={"init_command": "SET SESSION sql_mode=..."}`.
  Harmless for reads, but set it so plans and date handling are
  identical on both endpoints.
- **Long reads commit between chunks** (existing gotcha in
  [appointment-emails/NOTES.md](../appointment-emails/NOTES.md)): a
  read view held open on a reader pins undo purge on the writer. Applies
  to the CSV exports in this list (`strategy_activity`, `smartpixl`,
  `transcription_job`, `sf-accounts/export`).
- **Tests.** `scripts/test.sh` points `READER_DB_CONNECTION_STRING` at
  the same test DB, so a swapped dependency needs no fixture change.
  Tests that override `get_async_db` (`tests/conftest.py:130`) must also
  override `get_async_readonly_db` once their route moves.
- **Pool budget.** Each request on the reader now holds one reader
  connection (two for the Hub list). Watch `readonly_async_engine`
  pool exhaustion after Tier 2 + 3 land; the writer pool shrinks by the
  same amount.
- **Guard against regression.** Add a test that walks `app.routes`
  and asserts every GET uses a readonly dependency unless its handler is
  in an explicit allowlist (the "Must stay" table). Cheap, and it stops
  the next read route from landing on the writer by habit.

## Order of work

1. Tier 0 middleware (one file, test the login/permission-change path).
2. Tier 1 Hub siblings.
3. Sync reader pool sizing, then Tier 3 searches.
4. Tier 2 and the rest of Tier 3 in file-sized PRs.
5. Tier 4 extension trio, the regression test, the engine `init_command`.
