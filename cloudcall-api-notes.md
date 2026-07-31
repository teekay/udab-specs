# CloudCall API — auth & recording fetch (client's reverse-engineering notes)

> Provenance: braindump from the client's team, received 2026-07-28, preserved
> verbatim below. Empirically verified by them in July 2026 against the live
> API; running in their `abstrakt-call-transcription` repo
> (`src/cloudcall_client.py`). NOT from CloudCall's documentation. Treat the
> "dead ends" section as authoritative — it exists so we don't repeat the
> investigation. udab context: this feeds SPEC-transcription-v2 slice 4;
> credentials live in udab-server `.env` for now (`CLOUDCALL_LICENSE_KEY`,
> `CLOUDCALL_USERNAME`, `CLOUDCALL_PASSWORD`), destined for `sp_setting`.

> ## ⚠ CORRECTIONS (verified 2026-07-30, 10 live calls + docs research —
> ## see SPEC-transcription-v2 "Slice 4: call↔Task matching")
>
> The auth flow and credentials below are accurate. Three claims are NOT:
>
> 1. **`leg=c` is wrong for our purpose.** Each call exists as TWO records
>    (CloudCall runs on PortaOne; one record per call leg) sharing one
>    `SessionID`, each with its own id and its own, different, recording.
>    `leg=c` returns the *prospect-side* leg — NOT the record Salesforce
>    links, so its `id` does NOT "match the id in Salesforce's recording
>    URL" and its recording is not the one the pipeline was verified on.
>    Fetch WITHOUT `leg` and select the record with **`Leg == 1`** (the
>    rep-side leg; verified 10/10 against SF-stamped URLs).
> 2. **`SessionID` IS the correlation key** — it equals
>    `Task.synety__Call_Session_Id__c` (CloudCall's own package field,
>    "Unique Session Id Value for SYNETY Call"), verified 10/10. The
>    braindump compared it against the wrong SF field (`Call_ID__c`).
> 3. **The WhoId + ConnectTime heuristic is obsolete** — with
>    (SessionID, Leg==1) the match is exact; the redial-ambiguity problem
>    disappears. Do not implement the heuristic.

---

Everything below was verified empirically against the live CloudCall API in July 2026
and is running in the `abstrakt-call-transcription` repo (`src/cloudcall_client.py`).
This is not from CloudCall's documentation — it was reverse-engineered, and several
obvious-looking approaches are dead ends. Those are listed at the bottom so you don't
repeat the investigation.

## Short answer

You're right that a plain API key isn't enough. Auth is a **two-step, customer-tier
token flow** on the next-gen host:

**Host:** `https://ng-api.us.cloudcall.com`

**Step 1 — get a bearer token**

```
POST /v3/auth/login
Header: LicenseKey: <api key>
Body (form-encoded):
  grant_type = password
  username   = <customer login email>
  password   = <customer login password>
  type       = customer
```

Token comes back at `data.token` in the JSON response.

**Step 2 — list calls**

```
GET /v2/customers/<customer email>/calls?from=<ISO>&to=<ISO>&leg=c
Headers: Authorization: Bearer <token>
         LicenseKey: <api key>
```

Returns a **plain JSON array** of calls — no paging wrapper — across **every rep
account** under the customer. Bound the result with the time window. `leg=c` merges
call legs so you get one record per call.

The `LicenseKey` header is required on **both** requests.

## Credentials you need

Three values, not one:

| Value | Used as | Notes |
|---|---|---|
| API key | `LicenseKey` header | From CloudCall admin. Any of our keys work — see warning below. |
| Customer login email | `username` in the token request, and the path segment in step 2 | Currently `cgooding@abstraktmg.com` |
| Customer login password | `password` in the token request | Not in the repo — lives in `.env` / GitHub Actions secrets |

**Warning about generating new API keys:** we tested three separate API keys and all
three returned *identical* access. The `LicenseKey` is not the access gate — the
login password is, and whether that login is customer-tier or account-tier. Issuing a
new API key will not broaden access.

## Response fields that matter

Per call object:

| Field | Meaning |
|---|---|
| `id` | Numeric call id — matches the id in Salesforce's recording URL path |
| `ConnectTime` | Call start. **No timezone offset but it IS UTC** — treat naive values as UTC |
| `DisconnectTime` | Call end |
| `CallRecordingAvailable` | Boolean — check before using the URL |
| `CallRecordingURL` | Ready-to-download, auth token embedded, ~30-day expiry |
| `Contact.CrmObjectInstanceId` | **The Salesforce Contact Id** — this is the join key |
| `Contact.CrmProductName` | Check it equals `salesforce` before trusting the id |
| `CallDetail.AccountName` | Rep name |
| `SessionID` | A UUID. **Not** the `C-xxxx` reference — don't use it for correlation |

## Matching a call to a Salesforce Task

Verified on real data. Match on **two** conditions together:

1. `Contact.CrmObjectInstanceId` == `Task.WhoId`
2. `ConnectTime` ≈ the Task's call time

Timestamps matched to within ~1–15 seconds in testing. Allow a **5-minute
tolerance** to absorb clock drift; when several calls qualify (rep called the same
contact twice), the closest in time wins. The Contact id already isolates to one
prospect, so the tolerance is safe.

`to_utc()` handling must cover: `Z` suffixes, `+0000` offsets, and **naive
timestamps, which are UTC** — CloudCall's `ConnectTime` carries no offset.

**Do NOT correlate on `Call_ID__c`** (the `C-xxxx` value in Salesforce). It does not
appear anywhere in the CloudCall API response. This was an early wrong guess.

## Downloading the recording

`CallRecordingURL` is directly fetchable — the auth token is embedded in the query
string. **No headers needed.**

- Content type: `audio/mp3`, MPEG-L3 joint stereo, **2 channels**
- Channel 0 is the agent/rep, channel 1 is the prospect (no swap needed for CloudCall)
- URL expires roughly 30 days out

This is the same audio the existing pipeline already transcribes, so nothing changes
downstream.

## Why this matters

Salesforce stamps `Call_Recording_URL_Public__c` onto the Task via a batch that runs
at `:00/:15/:30/:45`. Pulling the recording from CloudCall directly removes that wait
— the CloudCall recording exists 1–2 minutes after the call ends.

## Dead ends — do not repeat these

- **Per-account auth on the legacy host.** `api.us.cloudcall.com` with three headers
  (`LicenseKey`, `Username` = the numeric *account id* not an email, `Password`) does
  work — but each account is one rep's seat, and the credential authenticated only
  **7 of ~41 seats**; the rest returned `403 "The username is not valid for that
  url"`. Superseded by the customer-tier flow.
- **Generating more API keys.** Three keys tested, identical access. Not the gate.
- **A second service account.** `product@abstraktmg.com` authenticates as itself only
  and returns `401 not authorized` for customer scope.
- **OAuth 1.0a.** The portal's "OAuth Keys" (Key + Shared Secret) are consumer
  credentials for the legacy signed-request flow under
  `api.us.cloudcall.com/oauth/...`. Not needed.
- **`type=account` on the NG login.** Works, but scopes to a single rep. Valid `type`
  values: `customer | account | portaoneadmin | cloudCallStaff`. Use `customer`.

## Known risks / open items

- **The customer credential is tied to one person's login**
  (`cgooding@abstraktmg.com`). Password change / offboarding breaks it. Worth asking
  CloudCall for a dedicated customer-tier API user.
- **Never log response bodies from the auth endpoint** — they can echo credentials.
  Recording URLs embed a signed token — don't log them either. Status codes only.
- **End-to-end production test was never completed** by the client's team: auth and
  fetch proven (incl. a 4,300-call pull), but the closed loop (pending call →
  CloudCall fetch → transcript row) was deferred. Do it early.
- **Orum has no API.** Only CloudCall can be accelerated this way. Orum recordings
  must still come through the Salesforce field, and they expire within days.
