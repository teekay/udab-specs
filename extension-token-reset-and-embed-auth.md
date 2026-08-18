# Handoff: sp_user_token reset button + why embed auth failures never log users out

Date: 2026-08-14 (analysis re-verified and substantially corrected same day). Companion to
[extension-403-auth-fix.md](extension-403-auth-fix.md) — that spec fixes client-side 403 *detection*;
this note covers the server-side failure mode where **no 403 is ever emitted**, so no amount of
client-side detection can help.

Corrections vs the first draft of this note: the reported error is **1013** (1031 was a typo in the
originating conversation); prod is **not** ahead of the checkout (both repos verified at
`upstream/master`); 3.5.2 **is** present in `extension-version/`. Error 1013 is the catch-all of
`update_kdm` — the KDM toggle in the survey embed — and the whole mechanism below is verifiable in
the checkout.

## 1. What was shipped (stop-gap)

Admin button to delete a user's cached Salesforce token, forcing eventual re-auth.

### Backend — `udab-server/app/routes/extension_version.py`

New endpoint `DELETE /extension/version/{version_id}/sf-token`, guarded by `MANAGE_EXTENSION_VERSIONS`
(same as the rest of the admin Versions tab):

1. Load `ExtensionVersion` by id → 404 if missing
2. Map its `email` → `sf_user.Email` → `sf_id` → 404 with the email in the message if no SF user matches
3. Delete the `sp_user_token` row keyed by that `sf_id`
4. Idempotent: returns `{"deleted": true|false}` with 200 either way (false = no row existed)

Tests: `udab-server/tests/test_extension_version_token.py` — 5 cases (permission denied, version 404,
SF user 404, successful delete, idempotent no-op), mocked-db TestClient pattern. All pass in host venv.

### Frontend — `udab-client/src/pages/extension/ExtensionPage.vue` (Versions tab)

- Third action button between Edit and Delete: `btn-outline-warning`, icon `ti ti-arrow-back-up`
  (curved undo arrow, confirmed present in bundled Tabler font), tooltip "Reset Salesforce token"
- Confirmation modal (`showResetTokenModal`/`resetTokenTarget`), copy states the user will need to
  re-authenticate with Salesforce
- Toasts: success "Token deleted — user must re-authenticate" vs info "No cached token existed"
- Layout: table container widened `w-50` → `w-75`, actions cell got `text-nowrap` (three buttons were
  stacking vertically)

### Local test data

`udab1.sp_user_token` seeded with a marker row for `anna.crews@sapperconsulting.com`
(`0054w00000AzJm7AAF`, token `test-token-for-reset-button-DELETE-ME`); rows for
`005Ro00000FHY4fIAH` (tomaskohl) and `005VA00000OcAySYAV` act as must-survive controls.
Bonus case: version row `tomaskohl@gmail.com.qafull` has no matching `sf_user` → reset there must 404.

## 2. Root cause: the two credential stores, and why only one of them breaks

Reported symptom: user logged into extension 3.5.2 on Orum, survey embed submits fail with
**"Error 1013: INVALID_SESSION_ID"** (client-confirmed error text, 2026-08-14), extension never
logs them out. Admin deleting the user's `sp_user_token` row makes the problem go away — a routine
the client ran manually via MySQL Workbench before the reset button existed; a row was always
present to delete, which the blank-recreation below predicts.

`INVALID_SESSION_ID` is the **blank-row signature**: only the blank-credential → `Bearer None` path
produces it (a stale-but-real password would fail with `invalid_grant` instead). This confirms the
mechanism in this section empirically, not just from code reading.

### There are two independent credential stores

Login (`POST /aiq-extension/authenticate` → `helpers/sf_auth.py:11-39`) encrypts
`[auth_until, email, password, user_id]` and hands the same string to two places:

| Store | Written by | Read by |
|---|---|---|
| `chrome.storage.local` (extension) | extension login | every extension API call (sent as `token` param) |
| `sp_user_token` (DB row, keyed by SF user id) | extension login (**upsert**, `sf_auth.py:28-39`); survey embed login (`survey.py:1058-1070`, upsert); talk-track embed (`talk_track.py:465-476`, **blank-credential insert** when row missing) | survey embed (`survey.py:105`), talk-track embed (`talk_track.py:462`), sequencer (`commands/sequencer/process.py:42`) |

**No extension endpoint ever reads the DB row.** The only `UserToken` reference in all of
`extension.py` is the *delete* inside `handle_sf_auth_errors` (`extension.py:114-118`). Extension
endpoints authenticate via `validate_token` (`extension.py:140-179`), which decrypts the token **from
the request** and calls `sfdc.set_credentials()` with the username/password embedded in it.

Concretely for `/aiq-extension/get-prospects` (the periodic call): the client
(`src/contacts.ts:383-397`) sends `token: data.token` read from `chrome.storage.local`
(`udabData()`, `abstrakt-intelligence.js:26-36`); the server (`extension.py:351-365`) runs the SF
password grant with the credentials inside *that* token.

### What the pre-signed embed URL does and doesn't prove

The embed URLs (`aiq_survey_url`, `aiq_talktrack_url`) are minted inside `get_prospects` — i.e.
downstream of a fully authenticated extension call — by `_generate_embed_url`
(`extension.py:452-468`), which HMAC-signs `contact_id|user_id|ts`. The signature carries **no
credential material**: it proves "an authenticated extension session for this SF user requested this
embed at time T," and `verify_iframe_signature` accepts it for 24 hours. The embed's SF credentials
are resolved separately, at iframe *load* time, from `sp_user_token[user_id]` — a lookup
disconnected from the authentication that minted the URL. So "the iframe never loads without auth"
is true for authorization-to-render and false for credentials: a URL minted during a healthy session
happily loads against a row that was deleted and blank-recreated minutes later. Note also that
`get_prospects` holds the user's live, just-verified username/password (`token_data`) at the moment
it mints the URL — and discards it; see fix 1b below.

### Consequence: extension health says nothing about row health

In the poisoned state the user's actual SF credentials are **fine** — it's only the DB row that is
blank or missing. So every periodic `get-prospects` (and every other extension call) succeeds with
200. There is no 403 to react to, because nothing the extension uses is broken. "3.5.2 logs out on
any 403" is true (verified in the 3.5.2 bundle: `apiFetch` + global `ajaxError` handler) and
irrelevant here.

The intuition "bad auth state → get-prospects 403s → instant logout" *is* correct for the case where
the credentials themselves break (password change, lockout, IP restriction): then the extension's own
token fails the password grant → `SalesforceAuthError` → `handle_sf_auth_errors` deletes the row and
returns 403 → extension logs out → user re-logs in → the upsert rewrites the row with good
credentials. **Credential breakage is therefore self-healing within minutes and cannot be the
persistent state.** The persistent state is precisely its complement: valid credentials + poisoned
row.

### The exact Error 1013 mechanism (blank row)

1. Talk-track embed loads with no row present → creates one with `username=""`, `password=""`
   (`talk_track.py:465-476`). Talk-track itself never uses SF credentials — its content comes from
   uDab's own DB, and its token-validated endpoints (`/talk-track/data`, `POST /session/...`) only
   decrypt the token to extract `user_id` for placeholder translation and session attribution
   (`talk_track.py:405-417` — no `set_credentials`, no expiry check). Why the route *persists* the
   row is unknown (it shipped with Talk Track activity tracking, PR #83, 2025-09-26); the observable
   effect is that the embed skips its login modal (`talk_track.html:307-315` shows it only when no
   token). Nothing in talk-track's flow ever reads the row back — its endpoints validate tokens by
   decryption alone — so the persistence serves no purpose we could verify.
2. Survey embed loads, reads the row (`survey.py:105`), token is non-null → renders **logged-in**
   (`survey.html:503/520` gate only on token presence) and bakes the token into page JS.
3. User toggles KDM → `update_kdm` (`survey.py:503`) → `_set_credentials_from_token`
   (`survey.py:71-80`) decrypts and sets the blank credentials — it checks neither `auth_until` nor
   blankness.
4. `get_access_token` with blank credentials returns `None` **without raising** (`sfdc.py:160-162`),
   so the SF request goes out as `Authorization: Bearer None` (`sfdc.py:197-199`). Salesforce answers
   `INVALID_SESSION_ID: Session expired or invalid`; the one retry does the same; `SalesforceAuthError`
   is raised (`sfdc.py:274`), caught by `update_kdm`'s bare `except`, and returned as HTTP **200**
   `{"success": false, "message": "Error 1013: INVALID_SESSION_ID: Session expired or invalid"}`.

Same 200-wrapping applies to `mark_qualified` (1010) and `save_question` (1021); the iframe's requests
are its own, on the API origin, with no `postMessage` bridge to the extension — so the 403-shaped
truth never reaches any client-side handler. The extension's own calls genuinely succeed. Zero 403s
anywhere is the designed-in outcome of this state, not a detection gap.

### Step 3: how the good row disappears

After extension login the row provably exists (upsert), so the blank-recreation in step 4 requires a
prior **deletion**. Exhaustive search of the codebase: exactly **three** code paths delete
`sp_user_token` rows — there is no cleanup job, no cron, no raw-SQL deleter (the migration only
creates the table), and the extension client never deletes server-side (logout is
`chrome.storage.local.clear()` only, `popup.js`). So step 3 is necessarily one of:

**Path A — `handle_sf_auth_errors`** (`extension.py:105-123`, wraps ~15 extension endpoints).
Fires when the password grant with the user's own credentials fails (`invalid_grant`: password
change/expiry, lockout, login-IP restriction, or transient SF conditions). Deletes the row and
returns 403; 3.5.2 logs the user out. Subtlety that keeps this path dangerous even on 3.5.2: **the
embeds outlive the extension logout** — already-minted iframe URLs stay valid 24h and never check
extension auth, so the user can keep working the survey/talk-track while logged out, and a talk-track
reload in that window blank-creates the row. The eventual extension re-login upserts a good row, so
this path poisons only the window until re-login (indefinitely on pre-3.5.2 clients, where the fetch
regression suppresses the logout — see extension-403-auth-fix.md).

**Path B — `update_kdm`'s delete-on-ANY-exception** (`survey.py:553-565`, commit `9f314b1`
"auto delete user token", 2026-02-01). A user toggles KDM and *anything* fails — most mundanely a
Salesforce validation rule rejecting the Contact patch (`SalesforceValidationError` → bare
`except`) — and the user's perfectly good row is deleted. No 403, no logout, no signal anywhere; the
extension keeps working; the user sees one "Error 1013: <original error>". Minutes later the
talk-track embed reloads → blank row → every subsequent submit fails 1013 `INVALID_SESSION_ID`, and
each further `update_kdm` failure re-deletes the blank row while talk-track re-creates it (flapping
between login-form and poisoned states, depending on load order). Nothing forces an extension
re-login for up to ~55h (double-TTL bug), so the state persists for days and recurs. (The fuzzy
fallback delete `LIKE '%' + aiqToken[10:20] + '%'` in this block is dead code: those characters fall
inside the random 16-byte IV, so it matches nothing — including other users' rows.)

**Path C — admin reset button** (shipped 2026-08-13; not the historical cause).

Ranking: B requires no auth anomaly and emits no external signal, and best fits "logged into the
extension, persistent 1013"; A announces itself via logout on 3.5.2 and heals on re-login. Timeline
is consistent with B: blank-create shipped 2025-09-26, the delete block 2026-02-01.

**Status 2026-08-14:** the client-confirmed `INVALID_SESSION_ID` text pins the poisoned state to the
blank row (variant discrimination done). The only remaining unknown is which deleter fires step 3
per incident — useful for telemetry, but the fix list below does not depend on it (fixes 1 and 2
close both paths).

**Prod verification (2026-08-14, RO replica):** 546 real rows, **22 blank rows** — oldest blank
2025-12-03 (after blank-create shipped, before the `update_kdm` deleter), newest **2026-08-14 19:57**
(actively occurring). A blank row's `created_at` doubles as a last-extension-login marker: any login
upserts the row back to real, so long-lived blanks = users who never logged in again (dormant reps or
admin/test-generated embed links); recent ones are live incidents.

**Remediation order:** deploy fix 1 first (otherwise any embed load recreates the blank row minutes
after a sweep), then clear the backlog in one statement on the writer:
`DELETE FROM sp_user_token WHERE CHAR_LENGTH(token) < 160;` — each affected user gets the embed
login form on next load and self-heals.

**Forensics to identify the path in prod** (per affected user):

1. `SELECT user_id, CHAR_LENGTH(token), created_at, updated_at FROM sp_user_token` on the read
   replica. Blank tokens are **152 chars** (2000/2000 samples with the app's own `encrypt`; the
   token is base64(IV·16 + HMAC·32 + AES-padded JSON·64) — the blank payload
   `[<ts>, "", "", "<18-char sf_id>"]` is ~50 chars, padded to 64). Real tokens carry email+password
   in those slots → ~200-216 chars. Robust rule: `CHAR_LENGTH(token) < 160` = blank. `UserToken` is
   `Timestampable`, so a blank row's `created_at` dates step 4 precisely.
2. Server logs just before that timestamp: Path A writes
   `Deleted cached token for user {id} due to auth error` (extension logger); Path B writes
   `Error updating KDM status for contact {id}: ...` plus a full traceback (survey logger). Whichever
   precedes the blank row's creation is that user's step 3 — and Path B's traceback also names the
   original triggering error.

### Why the trap persists for days, and why the admin reset works

Persistence: while the extension's self-contained token is valid, nothing forces the re-login that
would heal the row. Both expiry checks have the TTL on the wrong side of the comparison —
server `time.time() - EXTENSION_AUTH_TTL > auth_until` (`extension.py:169`), client
`authUntil < now - AUTH_TTL` (`abstrakt-intelligence.js:40`) — so tokens effectively live
**2×TTL ≈ 55 hours**, not ~28.

Recovery: `Sfdc` initializes with the **site-wide service-account credentials** from settings
(`sfdc.py:76-91`); the user token merely overrides them. With the row deleted,
`_set_credentials_from_token` no-ops, the survey page loads on service-account credentials, the
embed shows its login container (token is null), and the user's embed login — or their next
extension login — writes a fresh good row. Stable good state, since talk-track only creates a row
when none exists.

Note the flapping dynamic while trapped: `update_kdm`'s delete-on-error also deletes the *blank* row,
after which the next page load races talk-track's recreation against the survey's read — the user
bounces between "login form" and "logged-in but every submit fails 1013".

## 3. Recommended fixes (not yet implemented)

Impact order:

1. **`talk_track.py:465-476`: stop persisting the blank-credential token.** Minimal form: keep
   minting the identity-only token and passing it to the template (preserves the no-login-modal UX —
   talk-track validates tokens by decryption only, never against the DB), but drop the
   `db.add`/`db.commit`. This is a two-line deletion that kills the trap: a missing row then stays
   missing → survey shows its login container → recoverable by the user alone.

   1b. **Consider re-syncing the row from the extension's token.** The extension periodically proves
   possession of valid credentials (`token_data` in `validate_token`d endpoints); upserting
   `sp_user_token` from `token_data` when the row is missing/blank/older would make
   "pre-signed embed URL ⇒ valid credentials in the row" an actual invariant, self-heal the trap on
   every polling cycle, and remove the reason the talk-track blank-row hack exists. Constraint: our
   GET endpoints stay side-effect-free, so this must not live in `get-prospects`/`validate_token`
   directly — put it in a POST (a dedicated token-sync call, or piggyback on the existing
   `/aiq-extension/log` POSTs).
2. **Replace `update_kdm`'s delete-on-any-exception block (`survey.py:553-565`)** — currently a trap
   *generator*: it deletes good rows on non-auth failures. In all three submit endpoints
   (`update_kdm`, `mark_qualified`, `save_question`), catch `SalesforceAuthError` specifically before
   the bare `except`, delete the row on that path only, and return a distinguishable
   "please re-authenticate" response the embed can render instead of `Error 10xx: <raw exception>`.
3. **`_set_credentials_from_token` (`survey.py:71-80`): skip blank credentials and expired
   `auth_until`** instead of blindly overriding the service-account credentials. Caveat to decide:
   the fallback attributes SF writes to the service account rather than the user.
4. **Fix the doubled-TTL comparisons** on both sides (`extension.py:169`,
   `abstrakt-intelligence.js:40`) — they widen every poisoned window to ~2.3 days.
5. Optional, client: `postMessage` bridge from `survey.html` to the parent so the extension can react
   to embed auth failures directly.
6. Consider whether transient `invalid_grant` (rate limit/IP restriction) should really delete the
   row in `handle_sf_auth_errors`; with fixes 1-2 in place this becomes recoverable anyway.

The shipped reset button is the manual escape hatch for users currently stuck in the poisoned state.

## 4. Live browser reproduction (2026-08-17) — confirmed on local stack

The section-2/3 mechanism was reproduced **end-to-end in a real browser** against the local stack, with
DB checkpoints at each step. Not a script hitting endpoints — actual clicks in the actual Survey and Talk
Track iframes, exactly what a rep does in Orum.

### Harness

An "Orum simulator" host page embeds the two **real** uDab iframes (loaded from the live server through
the `localhost.previewport.net` ngrok tunnel — the app's own `AIQ_API_BASE`, so the survey page's
internal `fetch` to `/survey/update-kdm` resolves correctly over HTTPS). Orum itself and the extension are
not needed: the persistent trap is entirely iframe-↔-server (Path B), and the extension only *mints* the
signed URLs, which `_generate_embed_url`'s HMAC (`SECRET`, `contact|user|ts`) reproduces exactly.

Test data: one **synthetic** contact (`003Test0000TRAP001`, `Source__c='Fake test data'`) seeded under an
existing set-up account (Anna Crews's account `0014A00002iQDE3QAO`), so the survey renders fully (company/
questions/`survey_response` all resolve **local-first** with no SF call — `sync_and_get_contact` /
`sync_and_get_survey_response` only sync when the row is missing). `Company_Lookup__c` left NULL to skip the
Error-1004 company gate. The good `sp_user_token` row for the acting user is the real one already present.

### The sequence, with DB checkpoints (all confirmed)

| Browser action | `sp_user_token` after | Rep sees |
|---|---|---|
| Page load (healthy) | good, 192 chars | Survey logged-in, KDM unticked; Talk Track renders |
| Tick **KDM Identified** | **row deleted** | `alert: Error 1013: …`, HTTP **200**, no logout |
| **Advance prospect** (Talk Track reload) | **blank, 152 chars** (re-created) | Talk Track normal, no login modal |
| Reload Survey | reads blank row | renders **logged-in** again (blank token accepted) |
| Tick **KDM Identified** again | deleted again (flap) | `alert: Error 1013: INVALID_SESSION_ID: Session expired or invalid`, HTTP 200 |

The final state looks identical to the healthy state — checkbox reverts, survey looks fine — which is why
the trap is invisible to the rep. `update_kdm`'s delete uses `token.like(aiqToken)` (effectively equality;
base64 has no LIKE wildcards), and it fires because the survey page bakes the stored token into its JS and
sends it back verbatim — confirmed by the request payloads: the first submit carried the good token
(`u75fXM6…`), the last carried the freshly-created blank token (`QYg1SZbB…==`).

### New finding: an auth failure can spring the trap through `update_kdm`, silently

The good-row deletion in the live run fired on **`invalid_grant`**, not the validation-rule / `NOT_FOUND`
case section 2 emphasizes: the acting user's stored token credentials no longer authenticate against prod
SF, so `get_access_token` raised `SalesforceAuthError` *before* `patch_object` was even reached. Because
`update_kdm`'s bare `except` catches `SalesforceAuthError` too, the good row was deleted and 200-wrapped as
`Error 1013` — **with no 403 and no logout**.

This is a distinct trigger from Path A: same underlying failure class (`invalid_grant`), but surfacing
through the **survey submit** (silent delete, HTTP 200) rather than through the extension's `get-prospects`
(403 → logout → self-heal). It is mechanically inside Path B (the `update_kdm` deleter) and is closed by the
same fix — **fix 2**: catch `SalesforceAuthError` before the bare `except`, and on that path return a
distinguishable "please re-authenticate" response instead of deleting the row and emitting `Error 10xx`.

Corollary that matches the client's report: with credentials that always fail the grant, **every**
`update_kdm` / `mark_qualified` / `save_question` toggle deletes the row — which is exactly why "a row was
always present to delete" (§2). To exercise the pure non-auth (validation-rule) variant instead, use a token
with working prod creds so the grant succeeds and the synthetic contact's PATCH fails `NOT_FOUND`.

### Repro recipe

1. Seed a synthetic contact under an account that already has company/questions (`create-test-contact` skill
   + one `sf_survey_response` row with `Contact__c` = the synthetic id, `IsDeleted=0`). Leave
   `Company_Lookup__c` NULL.
2. Mint `ts`/`sig` for `(contact_id, user_id)` with `hmac.new(SECRET, f"{cid}|{uid}|{ts}", sha256)`
   (`SECRET` from `app/constants/constant.py`).
3. Serve a host page embedding two iframes against `AIQ_API_BASE` (the ngrok tunnel):
   `…/survey/?contact_id=…&user_id=…&ts=…&sig=…` and `…/talk-track/embed?…same…`, each with a reload control.
4. Click **KDM → Advance (reload Talk Track) → Reload Survey → KDM**, checking
   `SELECT CHAR_LENGTH(token) FROM sp_user_token WHERE user_id=…` after each (192 → gone → 152 → 152 → gone).
5. Clean up: delete the synthetic `sf_contact` / `sf__raw` / `sf_survey_response`, and restore the user's
   real token row. **Caution:** the good token is only in the DB — if you clobber it (e.g. a heredoc with
   `docker exec` missing `-i`, or an unset backup var), recover it from the ROW-format binlog: the DELETE
   before-image holds the full base64 token (`grep -ao "<prefix>[A-Za-z0-9+/=]*" /var/lib/mysql/binlog.*`).
