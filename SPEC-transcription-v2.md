# Transcription v2 — Milestone 1 (Quality, Dispositions, Orum)

Status: Milestone 1 IMPLEMENTED 2026-07-27 (uncommitted; client testing
expected 2026-07-28). Open questions below are deliberately deferred — the
defaults ship: confirmations/follow-ups OUT, labels as specced, no-URL Orum
tasks silently out of scope. Revisit after client testing.

## Background

The client sent a brain dump asking for five things. This spec covers the first
milestone — slices 1–3. Slices 4–5 are out of scope here and listed at the end.

1. **Slice 1** — Deepgram output parity with "Nick's config": diarized,
   speaker-labeled, interleaved transcripts instead of today's flat text.
2. **Slice 2** — Expand the `CallDisposition` filter from exact
   `'APPOINTMENT'` to a fixed list of the client's ~25 spelling variants
   (follow-ups later extend the same list; client is told to standardize
   their picklist).
3. **Slice 3** — Transcribe Orum recordings, currently excluded twice over
   (SOQL Subject filter + CloudCall-host URL gate).

Out of scope for this milestone:

- **Slice 4** — CloudCall API fallback when `Call_Recording_URL_Public__c` is
  missing/expired, and fetching recordings faster than the ~15-min batch that
  stamps the URL onto the Task (CloudCall has the recording 1–2 min after the
  call). Auth + endpoints reverse-engineered by the client's team — see
  `cloudcall-api-notes.md` in this directory. Correction to an earlier guess:
  the join key is NOT `synety__Call_Session_Id__c` — matching is
  `Contact.CrmObjectInstanceId == Task.WhoId` plus `ConnectTime` within a
  5-minute tolerance (closest wins). PoC verified 2026-07-30 with the
  client-provided credentials: auth OK; 3h window returned 2,819 calls, 97%
  with a recording URL (host `api.us.cloudcall.com`, drops into the existing
  vendor pipeline as-is), 96% with an SF Contact Id; recording fetch 206
  `audio/mp3` with no headers. Note: `Contact.CrmProductName` is returned
  capitalized ("Salesforce") — compare case-insensitively. Credentials:
  `CLOUDCALL_LICENSE_KEY`/`CLOUDCALL_USERNAME`/`CLOUDCALL_PASSWORD` in
  udab-server `.env` for now, destined for `sp_setting` (site integration
  credentials — in scope for that table, unlike business rules). Matching
  design constraints: see "Slice 4 (deferred): call↔Task matching" below.
- **Slice 5** — Auto-discovery: scheduled polling for new recordings +
  immediate transcription without a user request. Blocked on client sign-off
  on Deepgram OPEX.

Source of truth for slice 1 behavior: Nick's config doc (Google Doc, snapshot
in repo history of this spec). All its technical directions are informative,
not binding — but the *behavioral* spec (params, labeling rules, output
format) is what the client expects to see.

## Current state (what changes)

- Deepgram request is `model=nova-3&smart_format=true` only
  (`app/services/deepgram_transcribe.py`); output is the flat transcript of
  `channels[0]` stored as `sf-task-transcripts/{sf_task_sf_id}.txt`.
- `CALL_DISPOSITION_MAP` maps `appointment → 'APPOINTMENT'`,
  `pitch → 'KDM Pitched'` (one value each).
- SOQL excludes `[Orum]` subjects; non-`api.us.cloudcall.com` URLs are
  skipped with `not_cloudcall_url`.

## Slice 1 — Deepgram request & post-processing ("Nick's config" parity)

### Deepgram request

`POST https://api.deepgram.com/v1/listen` (keep direct REST — deliberate in
Nick's config too: the SDK v3.7 didn't expose `keyterm`), body
`{"url": recording_url}`, with query params:

| Param | Value | Notes |
|---|---|---|
| `model` | `nova-3` | Keep the existing constant |
| `multichannel` | `true` | Primary speaker separation — recordings are dual-channel, one party per channel |
| `diarize` | `true` | Within-channel splitting only — catches IVR/automated voice sharing a channel |
| `smart_format` | `true` | As today |
| `punctuate` | `true` | New |
| `utterances` | `true` | Hard requirement — all post-processing consumes `results.utterances`, not flat transcripts |

No `keyterm` in this milestone (see Keyterms below). Keep the 10-minute
timeout. **Billing note:** `multichannel` bills each channel's audio, so
per-minute cost roughly doubles. Flagged to client as OPEX.

### Keyterms

**Out of this milestone — no `keyterm` param sent, no hard-coded list.**
Nick's config biases recognition with repeated `keyterm` params, but his
list is compliance jargon for one end-client, and Abstrakt needs
per-end-client vocabulary that has no home yet: the client may add a
custom field on a Salesforce entity, or we may collate terms from
existing SF fields (account/company names, …). Designed when the client
decides the source. Notes for then: `keyterm` is nova-3-only, and httpx
`params` must be a list of tuples so the param repeats.

### Post-processing (new module `app/services/transcript_postprocess.py`)

Provenance: this section mirrors Nick's pipeline (his doc §4). It exists
because objective 1 of the brain dump is "mock Nick's behavior", and
because diarization alone yields per-channel utterance soup — *some*
labeling/ordering layer is mandatory for a readable transcript. The
non-negotiable core is items 1 (roles) and ordering; items 2 (automated-
voice rules) and the interruption-splitting in item 3 are the trimmable
extras if a leaner v1 is preferred.

Pure functions transforming raw Deepgram JSON → rendered transcript. No
I/O, fully unit-testable with fixture payloads.

1. **Channel → role mapping.** Utterances keyed by `(channel, speaker)`.
   Default: channel 0 = `Caller` (rep), channel 1 = `Prospect`. A
   `swap_channels` flag flips the mapping — decided per recording from the
   URL vendor *before* labeling (CloudCall: no swap; Orum: swap — see
   slice 3).
2. **Automated-voice overrides**, applied after role mapping:
   - *Opening-IVR rule*: on each channel, the "primary" (human) speaker is
     the diarized speaker with the most utterances; a non-primary voice
     whose last word ends before any human speaker begins anywhere is
     relabeled `Automated` (catches "Thank you for calling…" greetings).
     Heuristic, per Nick — accepted as-is.
   - *Phrase-match rule*: any turn whose text contains a known telephony
     phrase ("please hold", "press 1", "your call has been forwarded", menu
     prompts…) is relabeled `Automated`. Phrase list is a repo constant
     (`app/constants/automated_phrases.py`), seeded from Nick's list; it
     deliberately excludes "thank you for calling" (humans say it).
     Dev-owned tuning constant, not a setting.
3. **Interleaving.** Order utterances by start time. When a shorter
   utterance from the other speaker starts inside a longer container
   utterance, split the container at the interruption point using per-word
   timings so the interjection lands in context. Overlap tolerance `0.6s`
   (Nick's tuned constant — keep as a named module constant).
4. **Fallback.** If Deepgram returns no utterances, concatenate each
   channel's flat transcript (`channels[i].alternatives[0].transcript`)
   under its role label, no interleaving.

### Output & storage

The primary deliverable is the rendered transcript, plain text, one line
per turn: `[MM:SS] <Speaker>: <text>` (elapsed offset; minutes may exceed
59).

Stored exactly where flat transcripts live today —
`sf-task-transcripts/{sf_task_sf_id}.txt`, bucket `abstrakt-intelligence`.
Same key, same content type, so the API-key GET endpoint, the JWT read
view, and the udab-client Transcriptions tab keep working unchanged; they
just start serving labeled transcripts.

Alongside it, the structured result object from Nick's §5 is stored as
`sf-task-transcripts/{sf_task_sf_id}.json` — the object exists in the
worker anyway, so persisting it costs nothing and matches the braindump:

```json
{
  "transcript": "[00:03] Caller: Hi, this is …",
  "utterances": [{"speaker": "Caller", "transcript": "…", "start": 3.2}],
  "audio_duration_seconds": 421.5,
  "model": "nova-3"
}
```

`transcript` is rendered from `utterances` by one shared function, so the
two always agree and the transcript can be re-rendered without re-calling
Deepgram. The JSON is stored but not exposed through the API for now (no
consumer); a presigned `transcriptJsonUrl` on GET is a two-line change
when one appears.

### Existing flat transcripts

No versioning, no schema change. Tasks already transcribed under the old
flat format stay as they are — dedup keeps treating them as done; the
feature is forward-looking (per client: Day 1 transcribes Day 1's data).
Escape hatch if the quality team ever needs one old call re-done diarized:
delete that call's `completed` `sp_transcription_job_task` row and
re-request it — dedup then re-transcribes.

## Slice 2 — CallDisposition variants

`CallDisposition` in the client's org is effectively free text (their own
variant list includes `"Appointment follow up with Todd"`). We do not
chase the tail: the list is a **fixed code constant**, and the client is
told to standardize their picklist. Variants invented after that wait for
the next deploy — their problem to avoid, not ours to engineer around.

### Mechanism

- `CALL_DISPOSITION_MAP` in `app/schemas/transcription_job.py` changes
  from `CallResult → str` to `CallResult → list[str]`: `appointment` maps
  to the sanitized variant list (client's 25, deduplicated, empty strings
  dropped, plus the live strays below); `pitch` stays `["KDM Pitched"]`.
- SOQL changes from `CallDisposition = '{v}'` to
  `CallDisposition IN ('{v1}', '{v2}', …)` with each value escaped by the
  existing `_soql_escape`. SOQL string comparison is case-insensitive for
  this field (verified previously against live data), which also collapses
  casing dupes in the list. ~30 values in an `IN` is trivial for SOQL.
- The API surface does not change: `callResult` stays the two-value enum;
  `sp_transcription_job.call_result` stays `"appointment"` / `"pitch"`.

### Prod findings (2026-07-22, 90-day `GROUP BY CallDisposition` on appointment/follow-up-like values)

- Live values not in the client's lists, to raise with them:
  - **`Appointment Confirmation` — 1,949 rows**, the second-biggest
    appointment-family value. Oversight or deliberate exclusion? Material
    volume either way.
  - **Pitch follow-up family** (`Pitch Follow - Up` 1,184 + casing
    variants) — the client's follow-up expansion only covers appointments;
    the pitch bucket is lone `KDM Pitched`. Does pitch grow too?
- Live strays to include in the constant: `Appt Follow Up`, `Follow up
  Appointment`, `Appointment Confirmed` (if confirmations are in).
- Most of the client's 25 variants return zero rows in 90 days —
  historical typos; include them anyway (free).
- Case-insensitive SOQL matching re-confirmed by the data (`APT`/`apt`,
  `Appt A`/`APPT A`).

### Follow-ups ("eventually")

When the client says go, the follow-up variants are appended to the
constant (a one-line code change + deploy). If they instead want
follow-ups as a *distinct* category, that is a new enum value + map entry
— decide then. Mind the date-field caveat in Open Questions:
follow-ups/confirmations largely lack a matching `Appt_Set_Date__c`, so a
list change alone may not surface them.

## Slice 3 — Orum recordings

### Prod findings (2026-07-22, read-only queries against prod `sf_task`, 30-day window)

- Orum recordings live in the **same field** (`Call_Recording_URL_Public__c`):
  240,743 of 300,979 `[Orum]`-subject Tasks (80%) have it; the other two
  URL fields are empty for Orum. 20% of Orum tasks have no URL at all.
- **The `[Orum]` Subject flag is unreliable**: 81,663 tasks *without* the
  `[Orum]` subject also carry `orum.com` URLs — 89% of non-Orum-subject
  recording URLs are actually Orum. Vendor detection by URL host is
  mandatory, not just cleaner.
- URL format: `https://orum.com/recording/<opaque-id>` — bare `orum.com`
  host, no auth token, no expiry param. ID scheme changed over time
  (21-char base64-ish now, 32-hex a year ago); both share the path shape.
- `URL_Expiry_Time__c` is **CloudCall-only** (95% populated for CloudCall,
  0% for Orum) — no SF-side expiry signal for Orum.
- **Fetchability: SOLVED (spike, 2026-07-26).** The bare permalink serves
  an HTML player page, but **`?raw=true` appended to the same URL serves
  the WAV directly, anonymously** (206, `audio/wave`, range requests
  honored) — it's the page's own "Download" link. Verified end-to-end:
  Deepgram `/v1/listen` ingested `https://orum.com/recording/<id>?raw=true`
  with the full v2 param set — 200, 2 channels, 8 utterances, confidence
  0.91–0.999. The worker appends `?raw=true` to Orum URLs before sending
  to Deepgram; no bytes fallback needed for live URLs.
- **Orum audio is genuine dual-channel** (WAV header: 2ch/16kHz/16-bit),
  and the **channel swap is re-verified from data**: in the spike call,
  channel 0 = the answering prospect, channel 1 = the rep (named on the
  recording page as the caller). Orum: `swap_channels = true` confirmed.
- **Retention: Orum recordings expire quickly.** Recordings from 10 days
  before the spike (and 1 year before) return `400 Bad Request` even with
  `?raw=true`, while a 2-day-old one plays — the window looks like days,
  not weeks. **Accepted, not investigated further**: the feature is
  forward-looking (start transcribing on Day 1 with Day 1's data), so
  backfill of old Orum calls is explicitly out of scope. Expired
  recordings requested anyway surface as failed tasks with the Deepgram
  fetch error — acceptable. Note for slice 5: fast transcription doubles
  as data preservation for Orum, since audio not transcribed within the
  window is gone.
- Volume context: CloudCall is 9,847 URLs/30d vs **322,406** Orum by raw
  URL count — but measured against the job's actual scope (June 2026,
  `Appt_Set_Date__c` filter, appointment+pitch dispositions), **Orum
  roughly doubles in-scope volume rather than 33×-ing it**:

  | June 2026 | CloudCall | Orum |
  |---|---|---|
  | appointment calls / minutes | 2,475 / 13,194 | 2,171 / 12,139 |
  | pitch calls / minutes | 10,413 / 29,396 | 11,424 / 27,081 |
  | everything else / minutes | 26 / 32 | 299,640 / 180,389 (avg 39s — dials, voicemails) |

  OPEX at nova-3 ≈ $0.0043/min × 2 channels: today's scope on v2 params
  ≈ $370/mo ceiling; Milestone 1 (both vendors) ≈ $700/mo ceiling;
  hypothetical transcribe-everything (slice 5) ≈ $2,250/mo. Short calls
  dominate Orum's count but not its minutes.
- A third "vendor" exists in prod data:
  `orum-playground.fox-pangolin.ts.net` (a Tailscale host, 42 June calls)
  — someone's Orum sandbox writing into prod SF. Handled by the
  `unsupported_vendor` skip; ownership worth asking about.

### What changes

1. **SOQL**: drop `(NOT Subject LIKE '%[Orum]%')`. (Already known: the
   Subject prefix is an unreliable vendor signal — non-`[Orum]` subjects
   sometimes carry `orum.com` URLs. Vendor is determined by URL host,
   nothing else.)
2. **URL gate → vendor detection.** Replace `_is_cloudcall_url` with:

   | URL hostname | Vendor | `swap_channels` |
   |---|---|---|
   | `api.us.cloudcall.com` | `cloudcall` | no |
   | `orum.com` (bare host, confirmed in prod) | `orum` | yes |
   | anything else | — | task `skipped`, `skip_reason = "unsupported_vendor"` (replaces `not_cloudcall_url`) |

3. No schema change: vendor is derived from the snapshotted
   `recording_url` hostname wherever needed — at job creation (skip vs
   pending) and in the worker (`swap_channels` + `?raw=true`). One tiny
   helper, two call sites.
4. The Orum channel swap: Orum records the agent on the opposite channel
   from CloudCall. This is **empirically derived, not vendor-documented**
   (Nick verified it by listening to calls). Re-verify during the
   implementation spike; treat as a per-vendor constant, not per-call
   logic.

### Remaining spike items

Coverage, hostname, expiry, fetchability (`?raw=true`), dual-channel and
channel swap are all answered above; retention is accepted as-is
(forward-looking feature, no backfill). One minor item:

- Confirm the swap on 1–2 more calls (n=1 so far; Nick's independent
  verification makes this low-risk). Can happen during implementation
  testing.

## Data model changes

**None.** No new columns, no new settings, no migrations. (Earlier drafts
had `pipeline_version`/`vendor` columns and `sp_setting`-stored lists —
all dropped: `sp_setting` is site-wide configuration, not business-process
rules; vendor is derivable from the URL; versioning is unnecessary for a
forward-looking feature.)

## API changes

- None structural. `transcriptUrl` now serves the labeled transcript
  (same S3 key, still plain text).
- `MAX_TASKS_PER_JOB` raised 50 → 1000 (2026-07-27) for the client's Orum
  testing — they accepted the OPEX. Still a fixed constant, deliberately
  not configurable. The feature stays on-demand until the client's tests
  conclude.
- **New bonus endpoint (2026-07-27): `GET /api/sf-tasks/{sfTaskId}/transcript`**
  — transcript lookup by SF Task ID, independent of jobs; does NOT replace
  the job API. At most one transcript exists per Task (all job rows for an
  sf_task_sf_id share one flat S3 key), so this returns 0-1 results: 200
  with `{success, sfTaskId, transcriptUrl}` (fresh presigned URL, 7-day
  max) when the newest row with a key exists; 404 otherwise, with the
  error distinguishing never-requested / in-progress / failed. Same
  API-key auth as the job endpoints. Anticipates the client asking for
  per-task retrieval; if a per-task *request* flow is ever wanted (GET
  that also triggers transcription), that is a separate design.
- `skip_reason` value `not_cloudcall_url` is replaced by
  `unsupported_vendor` for new rows (old rows keep the old string; the read
  view passes strings through, so no client change needed).
- POST request/response shapes unchanged.

## Implementation plan

| File | Change |
|---|---|
| `app/services/deepgram_transcribe.py` | Full param set (no keyterm this milestone), return the full response JSON to the worker (not just flat text) |
| `app/services/transcript_postprocess.py` | **New** — role mapping, automated-voice rules, interleaving, rendering, fallback |
| `app/constants/automated_phrases.py` | **New** — telephony phrase list (Nick's seed) |
| `app/commands/transcribe_calls_job.py` | Wire postprocess; write `.txt` + `.json`; `swap_channels` + `?raw=true` decided by vendor derived from URL |
| `app/routes/api/transcription_job_api.py` | Vendor detection replaces `_is_cloudcall_url`; disposition `IN` list |
| `app/schemas/transcription_job.py` | `CALL_DISPOSITION_MAP` values become lists |
| `tests/…` | Postprocess unit tests from fixture Deepgram payloads (multichannel, diarized, interruptions, IVR, no-utterances fallback); route tests for vendor gate + IN-list SOQL |

Suggested order: slice 2 (small, independent) → slice 1 (the bulk) →
slice 3 (needs spike results).

## Risks

- **Dual-channel assumption is load-bearing.** Every labeling rule assumes
  one party per channel. If any vendor delivers mono/mixed audio, both
  parties collapse under one label. Mitigation: worker logs channel count
  from Deepgram metadata; a mono recording gets the fallback rendering and
  a warning, not silent mislabeling. (CloudCall dual-channel already
  verified by the original spike; Orum TBV.)
- **`nova-3` is a moving alias** — Deepgram can update the underlying
  model; transcripts may drift across updates. Accepted; pin a versioned
  model only if reproducibility becomes a requirement.
- **Plan/feature parity**: `multichannel` + nova-3 must be enabled on the
  Deepgram account's plan (Deepgram silently degrades output otherwise).
  The 2026-07-26 spike ran the full param set successfully on the
  dev-configured key; re-verify once on the prod key before rollout.
- **Heuristics are tuned constants** (0.6s overlap tolerance, phrase list,
  most-utterances-is-human). Shipped as-is per Nick's config; expect
  tuning requests once reviewers see output.
- **Cost**: multichannel ≈ 2× billed minutes vs today, plus lazy
  re-transcription of previously transcribed Tasks. Client informed,
  accepted ("deep pockets").

## Slice 4 (deferred): call↔Task matching — design constraints

Status 2026-07-30: feasibility proven (see the slice 4 bullet + PoC results
above and `cloudcall-api-notes.md`), implementation deferred. This section
exists so the matching design can be picked up later with fresh context.

**The hard requirement (Tomas):** a wrong match is worse than no match.
Failure scenario that must be impossible: rep calls prospect X at 14:05,
call drops at 14:07, rep redials within seconds. Both calls share
`WhoId`, the same dialed number, and start times inside any plausible
tolerance — the braindump's heuristic (`Contact.CrmObjectInstanceId ==
Task.WhoId` + `ConnectTime` within 5 min, closest wins) can attach the
wrong leg's recording and silently produce a wrong transcript for a
quality-scoring workflow. Duration/number invariants do not rescue this
case (same number; and `leg=c` merging behavior for reconnects is
unverified). Conclusion: **the heuristic alone is not acceptable as a
binding decision — only a unique key or deliberate deferral is.**

Decision rule for the implementation (0% wrong by construction):

1. **Task has an (expired) URL** → exact match: the numeric call id in the
   stored URL path (`/accounts/{acct}/calls/{ID}/recordingurl`) equals the
   API's `id`. Deterministic; no heuristic involved.
2. **Task has no URL** → match ONLY on a verified unique key (see below).
   If no unique key exists or the key is absent: **do nothing and let the
   Salesforce batch (runs :00/:15/:30/:45) stamp the true URL** — ground
   truth arrives within ~15 min, so deferral costs latency, never
   correctness. Never fall back to closest-wins.

**The unique key — VERIFIED 2026-07-30 (10/10 live calls + docs research):**

```
match:  API SessionID == Task.synety__Call_Session_Id__c   (the call)
select: the record with Leg == 1 within that session        (the side)
fetch:  WITHOUT any `leg` query parameter
```

Facts behind it, with provenance:

- CloudCall runs on PortaOne PortaSwitch (documented — PortaOne case
  study/CEO quote; two HA systems UK+US matching `ng-api.uk`/`.us`).
  PortaOne documents that one call = two "legs" = **two records sharing
  one session id**, and that click-to-call (a dialer's pattern) charges
  both legs to the rep's account. Each leg carries its own recording —
  confirmed live: two different audio files per call (rep leg includes
  ~10s of dialing; the two are NOT byte-identical).
- `synety__Call_Session_Id__c` is CloudCall's own field: their shipped SF
  package declares it "Unique Session Id Value for SYNETY Call... Do not
  change this", externalId=true. Matches the API `SessionID` UUID 10/10.
  Caveat: the package marks it `unique=false` — do not assume 1:1
  Task↔session; duplicates share one recording, handle benignly.
- The SF-linked record is **`Leg == 1`** — the leg *to the rep* (dialer
  connects the rep first, then dials out; leg 1 connects earlier and its
  `CLD` is usually the rep's own number, 9/10). Verified 10/10 against
  the ids embedded in SF-stamped recording URLs. Use `Leg == 1` as the
  selector; treat `CLD == AccountID` and earlier `ConnectTime` as sanity
  signals only.
- `i_account` does NOT discriminate the legs (both legs bill to the rep's
  account) — it identifies the rep, and matches `Task.synety__i_account__c`.

**Falsified braindump claims** (corrections also noted in
`cloudcall-api-notes.md`): (1) `leg=c` does *not* return the SF-linked
record — it returns the *prospect-side* leg 2, whose id and recording URL
differ from what SF stamps; never use `leg=c`. (2) "Don't use SessionID
for correlation" was wrong — it's the binding key; they compared it
against the wrong SF field (`Call_ID__c`). (3) The WhoId+time heuristic is
unnecessary — drop it entirely.

Defensive rule unchanged: expect exactly one `Leg == 1` record per
session; anything else (0, 2+, missing session) → defer to the SF batch.
Recording-channel note: only the leg-1 (rep-side) recording is the audio
the existing pipeline's channel assumptions were verified on; never feed
the leg-2 file to transcription.

### Implementation direction — decided 2026-07-30, NOT yet actioned

**Rejected: the scanner/stamper.** The client CTO proposed a timer
function (e.g. every-minute lambda) that scans for CloudCall Tasks
without a recording URL, fetches the URL from the CloudCall API, and
stamps it onto our local `sf_task` mirror. Assessment killed it on three
counts, agreed with Tomas:

1. *Staleness*: the mirror is synced by the hourly `sfdc-stream` job,
   while Salesforce itself stamps URLs every 15 min (batch at
   :00/:15/:30/:45, per `cloudcall-api-notes.md`) — scanning the mirror
   always loses the race; scanning SF live merely ties it.
2. *Nobody reads the stamp*: the POST endpoint queries Salesforce live
   and filters `Call_Recording_URL_Public__c != null`; a URL written to
   the local mirror is invisible to it.
3. *Mirror integrity*: `sfdc-stream` upserts full records — locally
   stamped values get clobbered on the next sync, and the mirror stops
   being a faithful copy of SF. (Open item: the CTO said "actual code
   already scans the sf_task table" — true of the client's own
   `abstrakt-call-transcription` repo perhaps, but NOT of udab-server's
   endpoint, which queries SF live. Confirm which repo they meant before
   the next conversation.)

**Chosen: fetch-on-demand inside the existing job flow** (same goal as
the CTO's idea — sellable as its implementation — but no new
infrastructure, no timers, no stamping; nothing is written to SF or the
mirror, only our own job tables + S3 as today):

1. SOQL eligibility widens from `Call_Recording_URL_Public__c != null` to
   `(Call_Recording_URL_Public__c != null OR
   synety__Call_Session_Id__c != null)`, also selecting
   `synety__Call_Session_Id__c` and `synety__Actual_Date_Time_of_Call__c`.
   A URL-less Task is CloudCall-eligible exactly when it has a session id
   — the marker is the join key. (Orum tasks without URLs remain
   untranscribable; Orum has no API.)
2. For URL-less eligible tasks: resolve the URL via the verified rule —
   CloudCall auth once per job; per task a narrow calls-window query
   around `synety__Actual_Date_Time_of_Call__c`, then
   `SessionID == synety__Call_Session_Id__c`, then `Leg == 1`. Use that
   record's `CallRecordingURL` as the task's recording URL.
3. Misses stay safe: recording not yet available (CloudCall has audio
   1–2 min post-call), no session match, or no unique leg-1 → task
   `skipped` with a distinct reason (e.g. `recording_not_yet_available`).
   Retry is free: the next POST re-queries SF and re-resolves. Never
   guess a leg.
4. Freebie: the same resolution path can refresh **expired** CloudCall
   URLs (30-day shelf life) instead of failing those tasks.
5. **Open fork (decide at implementation)**: resolve at POST time (zero
   schema changes; POST does a handful of CloudCall calls — the URL-less
   population is small since only minutes-old calls lack URLs; lean:
   Tomas + assessment favor this for the on-demand endpoint) vs. at
   worker time (faster POST, but the task row must snapshot session id +
   call time → two new nullable columns; the right answer if/when the
   slice-5 scanner materializes).
6. **Pre-implementation probe**: check whether `ng-api` supports
   filtering the calls listing by session id directly (PortaOne's
   underlying API accepts `h323_conf_id` in lieu of a date range) — that
   would replace window queries with exact lookups.

Prerequisite unchanged: move `CLOUDCALL_LICENSE_KEY` /
`CLOUDCALL_USERNAME` / `CLOUDCALL_PASSWORD` from `.env` to `sp_setting`
(site integration credentials) so the client can edit/rotate them.
Also worth doing before production: ask CloudCall for a dedicated
customer-tier API user — the current credential is one person's login
(`cgooding@`), per the braindump's own risk note.

## Open questions

- [ ] **~20% of Orum tasks have no recording URL** — accept as untranscribable,
      or raise with client? (Low stakes; they just won't appear in jobs,
      since the SOQL already requires the URL field.)
- [ ] **`Appointment Confirmation` (1,949/90d by CreatedDate)**: include in
      the appointment bucket or deliberately excluded? Ask client. NOTE:
      June data shows ~zero confirmations under the `Appt_Set_Date__c`
      filter — confirmation calls apparently don't carry the set-date the
      job filters on, so including them is not just a list change; it
      needs a decision on which date field scopes them (likely
      `CreatedDate`/`ActivityDate`). Follow-ups are nearly as affected
      (95 Orum June calls vs 2,031/90d by CreatedDate).
- [ ] **Pitch follow-ups** (~1,188/90d): should the pitch bucket expand
      like the appointment one? Ask client.
- [ ] **`Automated` label wording**: Nick uses `Automated`; confirm the
      call-quality team doesn't expect different labels than
      `Caller`/`Prospect`/`Automated`.

## Resolved

- [x] Volume/OPEX: out of band — client accepts cost (their money).
- [x] Slices 4–5 deferred to milestone 2. (The `synety__Call_Session_Id__c`
      join-key guess was later falsified — see the slice 4 note above.)
- [x] Re-transcription of old flat transcripts: none — they stay as-is;
      manual escape hatch (delete the completed task row, re-request).
- [x] Keyterms: out of Milestone 1 entirely — no param sent, no constant.
      Per-client sourcing (SF custom field vs collation from existing
      fields) is designed when the client decides.
- [x] Dispositions, automated phrases, overlap tolerance: fixed code
      constants. `sp_setting` holds site-wide configuration only, never
      business-process rules.
- [x] Structured JSON result (Nick's §5): stored alongside the `.txt` —
      it exists in the worker anyway, so complying with the braindump is
      free. Not API-exposed until a consumer exists.
- [x] Storage stays S3 under existing keys; `.txt` path preserved so
      existing consumers (API clients, udab-client read view) need no
      changes.
- [x] Orum URL field/coverage/hostname/expiry: answered by prod queries
      (see slice 3 findings). Fetchability answered too — negatively,
      hence the open blocker above.
- [x] Disposition free-text tail measured (90d): small beyond the client's
      list; seed list updated with live strays.
- [x] Orum audio access: `?raw=true` on the stored URL, anonymous;
      Deepgram URL ingestion verified end-to-end with full v2 params
      (spike, 2026-07-26). Channel swap confirmed from data.
- [x] Orum retention (days-scale): accepted — feature is forward-looking,
      Day 1 transcribes Day 1's data; no backfill of old Orum calls.
      Expired recordings fail gracefully in the worker.
