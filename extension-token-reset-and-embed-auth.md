# Handoff: sp_user_token reset button + why embed auth failures never log users out

Date: 2026-08-14. Companion to [extension-403-auth-fix.md](extension-403-auth-fix.md) — that spec fixes
client-side 403 *detection*; this note covers the server-side failure mode where **no 403 is ever emitted**,
so no amount of client-side detection can help.

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

## 2. Root cause investigation: "SF auth fails but user is never logged out"

Reported symptom: user on extension **3.5.2** submits the survey embed, gets an "Error 1031"-style
alert, extension never logs them out. 3.5.2 reportedly logs out on any API 403 — yet doesn't here.

### The token model (verify before trusting memory)

- Login (`/aiq-extension/authenticate` → `sf_auth.py:20-39`) encrypts `[auth_until, email, password,
  user_id]` **once**; the same string goes to the extension (chrome.storage) and to `sp_user_token`.
  Identical at birth.
- The extension's own endpoints (`extension.py` `validate_token`) **never read the DB row** — the
  client token is self-contained. `sp_user_token` is read only by the embed/background flows:
  survey iframe (`survey.py:105`), talk-track embed (`talk_track.py:462`), sequencer.
- `Sfdc` does an OAuth **password grant with the user's own credentials** per request
  (`sfdc.py:169-173`), access tokens cached 30 min per credentials-hash.

### The poisoned state and how it forms

Divergence is **row lifecycle**, not credential staleness (a changed password would break both copies
equally — the copies are the same token):

1. Any `SalesforceAuthError` on an extension endpoint deletes the row
   (`extension.py:105-123` `handle_sf_auth_errors`). Crucially, `SalesforceAuthError` fires on any
   `invalid_grant` (`sfdc.py:192`, `sfdc.py:274`) — which Salesforce also returns for **transient**
   conditions (login IP restriction, temp lockout, login rate limit). So the row can be deleted while
   the credentials are actually fine.
2. If the client misses/ignores the 403 (the fetch regression in extension-403-auth-fix.md, or the
   transient just clears), the user keeps working. Nothing ever rewrites the row — login is its only
   writer.
3. **`talk_track.py:465-476` then recreates the row with blank username/password** when the talk-track
   embed loads and finds no row. This is the trap: survey embeds now see a non-null `aiqToken`, render
   as logged-in (`survey.html:503` shows its login container only when the token is null), and every
   submit fails.

### Why zero 403s reach the extension (the disconnect)

1. **The failing call returns 200.** Survey routes catch everything with bare `except Exception` and
   return HTTP 200 `{"success": false, "message": "Error 10xx: ..."}` (e.g. `survey.py:423-426`,
   `survey.py:567`). The "Error 1031" string is one of these (repo checkout goes to 1021; prod is
   ahead). The 403 is destroyed server-side.
2. **Iframe boundary.** Survey requests are made by the iframe's own JS on the API origin; the
   extension's 403 handlers wrap only its own calls. No `postMessage` bridge exists in `survey.html`.
3. **The extension's own calls genuinely succeed** — its token is valid; only the DB row is blank.

So "log out on any 403" is correct but vacuous: extension calls → 200, survey load → 200,
survey submit → 200 + `success:false`. Client-side fixes cannot create the missing signal.

Note: `extension-version/` in the extension repo only goes up to 3.5.1 (3.5.2 not in checkout).
Versions ≤3.3.1 have no 403 handling at all, but per ops the affected users are on 3.5.2, so the
older-version gap is not the active bug.

## 3. Recommended fixes (not yet implemented)

Impact order:

1. **`talk_track.py:465-476`: stop creating blank-credential `sp_user_token` rows.** This is what
   makes the trap state permanent. Without it, a deleted row → survey shows its login container →
   recoverable.
2. **Survey/talk-track routes: catch `SalesforceAuthError` before the bare `except Exception`**,
   delete the `sp_user_token` row (mirroring `handle_sf_auth_errors`), and return a distinguishable
   response the embed can render as "please re-authenticate in the extension" instead of
   `Error 10xx`.
3. Optional, client: `postMessage` bridge from `survey.html` to the parent so the extension can react
   to embed auth failures directly.
4. Consider whether transient `invalid_grant` (rate limit/IP restriction) should really delete the
   row in `handle_sf_auth_errors`, since deletion + blank recreation is the entry path to the trap.

The shipped reset button is the manual escape hatch for users currently stuck in the poisoned state.
