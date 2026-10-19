---
kind: notes
status: in-progress
area: talk-track
updated: 2026-10-16
repos: [udab-server]
summary: "Living reference for the talk track area: embed signing, session semantics, postMessage contract, dev gotchas."
---

# Talk Track — living notes

## Embed URL signing (`/talk-track/embed`)

- Two HMAC-SHA256 signatures over pipe-joined fields, one shared `SECRET`
  (`app/constants/constant.py`, the only copy since 2026-10-15):
  - `sig` = `contact_id|user_id|ts`, 24 h window. Fails → 403 Error 1001.
  - `origin_sig` = `contact_id|user_id|ts|parent_origin`, optional, both or
    neither. Fails, or `parent_origin` not `https` on `*.force.com` /
    `*.salesforce.com` → 403 Error 1003. Verified after `sig`, so expiry is
    inherited.
- Error 1002 is the data path (contact/account/talk track missing), not auth.
- URL issuers: Orum extension (`generate_talktrack_url`, no `parent_origin`)
  and Salesforce Apex (adds `parent_origin` from QAFull on, per
  [session-id-postmessage.md](session-id-postmessage.md)).
- Secret is identical in every environment; decided 2026-10-15 not to split
  it per environment yet.

## Sessions

- A `sp_talk_track_session` row is created client-side by `createSession()`
  on iframe load, after the login modal, and on every talk-track tab switch
  (new id each time). Reset keeps the id and bumps `reset_count`.
- `origin` column = Referer hostname; `client_session_key` correlates Orum
  extension events; `dialed_number` marks real calls. The signed
  `parent_origin` is deliberately not stored (decided 2026-10-16): it is a
  request-time check, and the row already has the contact and the host.
- `str(session.id)` is the value Salesforce knows as
  `Talk_Track_Session_Id__c` (daily job
  [sf-task-session-link.md](sf-task-session-link.md), and live via
  postMessage).

## postMessage to the embedding page

- Sent by `notifyParent()` in `talk_track.js` after every session creation,
  only when the server verified `parent_origin`; target origin is derived
  server-side, never `*`.
- Payload is Salesforce's contract, do not add keys:
  `{type: 'abstrakt.talkTrack.session', sessionId: '<id as string>', contactId}`.
- Their listener drops anything not from the configured AIQ origin, so a
  QAFull → stage setup needs `https://api-stage.abstraktintelligence.com`
  allowed on their side.

## Debugging embed rejections

- Every 1001/1003 goes to the `talk_track` logger at ERROR as
  `Embed rejected (Error 100x): <reason>; contact_id=.. user_id=.. ts=.. age=..s
  ... signed_message='<exact bytes we HMAC'd>' referer=..`. Accepted
  `parent_origin`s log at INFO as `Embed parent_origin accepted`.
- `grep "Embed rejected"` in the server log; compare `signed_message` with
  what the Apex side signed. A mismatch there is the whole story. The browser
  only ever sees the generic Error 100x text.
- In the iframe's console, `[talk-track] posted session id to parent` confirms
  the postMessage went out and with which target origin; the warn variant
  explains why it was skipped.

## Dev gotchas

- Dev server is plain HTTP on `http://localhost:8800` (uvicorn SSL flags are
  commented out in `docker-compose.override.yml`); `watchmedo` restarts on
  `.py` changes, static JS/templates are served live.
- The local dev DB (as of 2026-10-15) has no published talk track whose
  account has a contact with `sf__raw`, so `/embed` ends in Error 1002. Use
  the `create-test-contact` / `populate-placeholder-data` skills or publish a
  draft track before a full render.
- `generate_test_signature.py --parent-origin <page url>` emits a signed pair
  for local testing.
- `tests/test_ai_assistant_sessions.py` asserts the test DB is named
  `udab_test` or `test_db`; `scripts/test.sh` defaults to `udab_test_001`, so
  run it with `TEST_DB=udab_test ./scripts/test.sh ...` or it errors out
  before any test runs.
