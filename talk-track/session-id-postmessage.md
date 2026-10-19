---
kind: spec
status: done
area: talk-track
updated: 2026-10-16
repos: [udab-server]
summary: "Embed iframe posts its session id to the Salesforce page; signed parent_origin names the embedding page."
---

# Talk Track Session ID → Embedding Page (postMessage)

## Problem

The Talk Track iframe (`/talk-track/embed`) creates a `sp_talk_track_session`
row when it loads, but the page that embeds it never learns the session id.
Salesforce (Dialer 3.0, Dialer 4.0, the Contact page, and the Talk Track
pop-out) now wants that id in real time so it can create its own Talk Track
Session record (contact, rep, start time, source) and link call Tasks to the
session at call time instead of relying on the daily matching job
([sf-task-session-link.md](sf-task-session-link.md)).

Rollout target is the QAFull sandbox only; production Salesforce is unchanged
until QA signs off.

## What Salesforce sends (already built on their side)

The embed URL gains two parameters, alongside the unchanged
`contact_id`, `user_id`, `ts`, `sig`:

| param | value |
|---|---|
| `parent_origin` | Address of the embedding Salesforce page, e.g. `https://amg--qafull.sandbox.lightning.force.com/lightning/r/Contact/003XXXXXXXXXXXX/view`. Differs per sandbox. May be a full page URL (with the Contact route) or a bare origin; the server handles both. |
| `origin_sig` | `HMAC-SHA256(SECRET, "contact_id|user_id|ts|parent_origin")`, hex, same shared `SECRET` as `sig`. Signed over the raw (URL-decoded) string. |

## What Salesforce accepts (Daniela, 2026-10-15)

The listener runs in the Dialer 3.0 and 4.0 Talk Track components, the Contact
page, and the pop-out. The pop-out is a separate browser window, but it is
still a Salesforce page that embeds the same iframe, so `window.parent` is
always the right target. Anything violating these rules is dropped silently:

- `event.origin` must be exactly the AIQ API origin configured in that SF org
  (`https://api.abstraktintelligence.com` in prod and, today, in QAFull). The
  browser sets this from the iframe URL; nothing on our side controls it.
- Plain object, not a JSON string.
- `type` exactly `abstrakt.talkTrack.session`.
- `sessionId` as a **string** (`"2629962"`), letters/digits/underscore/hyphen,
  ≤255 chars, same value the daily job writes to `Talk_Track_Session_Id__c`.
  That is `str(sp_talk_track_session.id)`
  (`app/commands/sfdc/link_talk_track_sessions.py:461`), so the raw numeric id
  stringified.
- `contactId` equal to the `contact_id` from the iframe URL (15 or 18 chars);
  dropped if it is not the contact currently loaded on their page.
- Target origin must be the URL-decoded `parent_origin`, never `*`, and only
  after `origin_sig` verifies.
- One message per session; duplicate `sessionId`s are ignored on their side.
- Source (Dialer / Pop-Out / Contact) is recorded on their side from which
  component received the message. We send nothing for it and keep our
  `origin` column as is.

## Solution

### 1. Server: verify `parent_origin` on `/talk-track/embed`

`app/routes/talk_track.py`, `get_talk_track_embed`:

- Add optional query params `parent_origin: str | None` and
  `origin_sig: str | None`.
- Both or neither. One without the other → 403.
- Verify after the existing `sig` check (which already enforces the 24 h `ts`
  window): `hmac.compare_digest(expected, origin_sig)` where
  `expected = HMAC-SHA256(SECRET, f"{contact_id}|{user_id}|{ts}|{parent_origin}")`
  over the decoded query value FastAPI hands us.
  Mismatch → 403 with a distinct code (`Error 1003: invalid parent origin`) so
  it is distinguishable from a bad `sig` in logs and screenshots.
- Derive `target_origin = f"{scheme}://{netloc}"` from the verified value.
  `postMessage` only ever uses the origin of whatever URL it is given, so this
  is equivalent to passing the decoded `parent_origin` as Daniela describes,
  minus any parsing in the browser. Idempotent when `parent_origin` is already
  a bare origin.
- Reject if `scheme != "https"` or the host does not end in an allowed suffix
  (see Decisions). Rejection is a 403 like a bad signature.
- Put the new `verify_parent_origin_signature()` next to
  `verify_iframe_signature()` (currently in `app/routes/survey.py`) and import
  `SECRET` from `app.constants.constant`. See the secret note under Open
  questions before touching the other literals.
- Pass `parent_target_origin` to the template. When the params are absent it
  is empty and the page behaves exactly as today, so the Orum extension embed
  is untouched.

### 2. Template + JS: post the session id after every session creation

`app/templates/talk_track.html`: expose `window.parentTargetOrigin` next to
`window.contactId`.

`app/static/js/talk_track.js`, `createSession()`: right after
`this.sessionId = result.session_id`, call `this.notifyParent()`:

```js
notifyParent() {
    const targetOrigin = window.parentTargetOrigin;
    if (!targetOrigin || !this.sessionId || window.parent === window) return;
    window.parent.postMessage({
        type: 'abstrakt.talkTrack.session',
        sessionId: String(this.sessionId),
        contactId: this.contactId
    }, targetOrigin);
}
```

- Exactly the three keys Salesforce specified, `sessionId` stringified. No
  extra keys, no `JSON.stringify`.
- `this.contactId` is the template's `contact_id`, which is the URL param, so
  it matches what their page compares against.
- Fires on every successful `createSession()`: initial load, after the login
  modal, and on talk-track tab switch. Each of those is a new session with a
  new id, so this is still "once per session". Reset keeps the same id and
  posts nothing.
- `/talk-track/show/{id}` (preview) never has `parent_origin` and never posts.

### 3. Nothing stored

`parent_origin` is a runtime check only: verified, reduced to a target
origin, handed to the JS, forgotten. A column for it was built and dropped on
2026-10-16 (Tomas): the Contact id is already on the session row, the host is
already in `origin` (Referer), and nothing would read it.

## Decisions

- **Signed, not open.** Without `origin_sig`, anyone holding a valid embed URL
  (24 h lifetime, shared in SF pages) could append their own `parent_origin`,
  frame the talk track, and collect session ids. The HMAC makes
  `parent_origin` something only the URL issuer can set.
- **Host-suffix allowlist on top of the HMAC.** `SECRET` is committed in
  plaintext in udab-server and shared with the Salesforce team, so "only our
  counterpart knows it" is the weakest link, not the HMAC. A constant list of
  allowed host suffixes (`.force.com`, `.salesforce.com`) plus `https`
  bounds the blast radius to Salesforce domains at the cost of three lines.
  Both QAFull hosts (`amg--qafull.sandbox.lightning.force.com`,
  `amg--qafull.sandbox.my.salesforce.com`) and prod fall under them.
  Suffixes rather than exact QA/PROD URLs so sandbox refreshes and renames
  never need a udab-server deploy. Stays a constant, not a Setting, until a
  non-Salesforce embedder (Orum) actually wants this feature.
- **Target origin derived server-side.** `postMessage` requires an origin;
  deriving it after verification means the JS never parses or trusts URL
  input.
- **Message shape is Salesforce's.** `abstrakt.talkTrack.session`,
  `sessionId` string, `contactId`; nothing else. We adapt to their listener,
  not the other way round.
- **No change to `sig`.** The second signature leaves existing URL issuers
  (Orum extension, Apex in production) working unchanged.

## Not in scope

- Salesforce listener, the SF Talk Track Session record, and the
  Dialer/Pop-Out/Contact source field (tracked entirely on their side).
- Orum extension: `generate_talktrack_url` in `app/routes/extension.py` does
  not add `parent_origin`. The extension already correlates via
  `client_session_key` and needs nothing here.
- Retiring the daily session-link job. Once SF stamps
  `Talk_Track_Session_Id__c` live, the job becomes a fallback; decide
  separately after QA, and make sure the two never disagree.

## Tests

`tests/` next to the existing embed tests:

- valid `sig` + valid `origin_sig` → 200, template gets the derived target
  origin;
- valid `sig`, tampered `parent_origin` → 403 Error 1003;
- `parent_origin` without `origin_sig` and vice versa → 403;
- valid pair, `http://` or non-allowlisted host → 403;
- `parent_origin` given as a bare origin and as a full URL → same derived
  target origin;
- neither param → 200, no `parentTargetOrigin` in the page (Orum path);

Manual: load the embed in a page on an allowed host with a `message` listener
and confirm one message per session creation (load, login, tab switch), none
on reset, `sessionId` is a string.

At dev time also update `.claude/skills/generate-embed-url/SKILL.md` to
optionally emit `parent_origin` + `origin_sig`.

## Staging (QAFull → stage AIQ)

Agreed with Daniela: QAFull will point at stage AIQ so this can be tested
outside prod. Facts on our side:

- Stage API domain is `api-stage.abstraktintelligence.com`, deployed from the
  `stage` branch of udab-server via Argo CD (secrets.txt, "AIQ Server").
  The SF listener in QAFull must then accept
  `https://api-stage.abstraktintelligence.com` as `event.origin` instead of
  the prod host, or every message is dropped.
- There is **no stage-specific shared secret**. `SECRET` is a hardcoded
  constant, identical in every environment, and duplicated as a literal in
  `app/constants/constant.py`, `app/routes/talk_track.py` and
  `app/routes/survey.py`. The "staging secret" Daniela asked for is the prod
  secret QAFull already holds.

## Decided 2026-10-15 (Tomas)

- No per-environment secret for now; the three `SECRET` literals are
  consolidated onto `app/constants/constant.py` instead.
- Stage DB is writable by us, so the contacts QAFull will send get seeded
  there as needed (see the `generate-embed-url` / `create-test-contact`
  skills for the preflight checks).
- Salesforce creates one Talk Track Session record per message, including
  the extra sessions a tab switch produces. They have to.
- Exact `parent_origin` string: we find out in QA. The verifier is
  byte-exact on the decoded query value, so any mismatch surfaces as
  Error 1003 in their embed and in our logs.

## Implementation (built 2026-10-15, merged 2026-10-16)

udab-server:

- `app/constants/constant.py`: `SECRET` is the only copy;
  `EMBED_PARENT_HOST_SUFFIXES = (".force.com", ".salesforce.com")`.
- `app/routes/survey.py`: `verify_parent_origin_signature()` and
  `parent_target_origin()` next to `verify_iframe_signature()`; literal
  secret removed, imported from constants.
- `app/routes/talk_track.py`: `/embed` takes `parent_origin` + `origin_sig`
  (both or neither), returns 403 Error 1003 on any failure, passes
  `parent_target_origin` to the template. Literal secret removed.
- `app/templates/talk_track.html`: `window.parentTargetOrigin` via `tojson`;
  JS cache-buster bumped.
- `app/static/js/talk_track.js`: `notifyParent()` after every successful
  `createSession()`; payload exactly `{type, sessionId: String, contactId}`.
- No schema change.
- `generate_test_signature.py --parent-origin` emits the signed pair;
  `.claude/skills/generate-embed-url/SKILL.md` documents it.
- Tests: `tests/test_talk_track_embed_parent_origin.py` (helpers, route
  403 ordering, template rendering). Verified live against the dev server:
  disallowed host and tampered `origin_sig` → 1003, allowed host passes the
  gate. Full render not exercised locally: the dev DB has no published talk
  track with a contact that has raw data.

## Open questions

1. **QAFull listener origin.** When QAFull is pointed at stage, their
   `event.origin` check must accept `https://api-stage.abstraktintelligence.com`
   or every message is dropped silently.
2. **Stage data.** Seed the test contacts (with `sf__raw`) and a published
   talk track on their account in the stage DB before the QA session.
3. ~~Exact `parent_origin` string~~ Resolved 2026-10-16 in the QAFull → stage
   test: Salesforce sends the bare origin
   (`https://amg--qafull.sandbox.lightning.force.com`), not a page URL.

## Verified on stage (2026-10-16)

Driven through the real Chrome tab on the QAFull Contact page: the embed
loaded from `api-stage` with both signatures, a reload of the iframe created
session 122, the iframe console logged `[talk-track] posted session id to
parent`, the Lightning page received
`{type: 'abstrakt.talkTrack.session', sessionId: '122', contactId: '003Ro000008kEGHIA2'}`
from `https://api-stage.abstraktintelligence.com`, and Salesforce then called
Apex `TalkTrackSessionController.recordSession` with that id, the contact and
`source: 'Contact Page'`, state SUCCESS. The session row on stage carries the
contact, the account and `origin=amg--qafull.sandbox.lightning.force.com`.

## PROD rollout

- Code only: no migration, no settings change, same secret. The JS
  cache-buster is bumped, so browsers pick up `notifyParent()` on first load.
- Order does not matter. Without `parent_origin` on the URL the embed behaves
  exactly as before, and FastAPI ignores the two params if Salesforce ships
  first. Nothing posts until both sides are live.
- Salesforce PROD listener must accept `https://api.abstraktintelligence.com`
  as `event.origin`.
- After the SF PROD deploy, watch the API log for `Embed rejected (Error
  1003)`; one line per failed embed, with the signed input.
- The daily session-link job keeps running; it only fills empty
  `Talk_Track_Session_Id__c` fields, so it cannot overwrite what Salesforce
  now records live.

## Handoff to Salesforce (once agreed)

- We send `{type: 'abstrakt.talkTrack.session', sessionId: '<id>', contactId}`
  as a plain object to `window.parent`, target origin derived from the
  verified `parent_origin`, once per session created (load, re-login, tab
  switch).
- `parent_origin` must be `https://` on a `*.force.com` or `*.salesforce.com`
  host, or the embed returns 403 Error 1003.
- For QAFull → stage: AIQ API domain `api-stage.abstraktintelligence.com`;
  the shared secret is the same in every environment.
