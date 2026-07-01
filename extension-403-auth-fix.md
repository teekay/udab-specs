# Fix: Extension 403 auth error handling

## Problem

When a user's Salesforce credentials become invalid (password changed, session revoked, or AIQ token expired), the server returns HTTP 403. The Chrome extension should detect this, clear the user's session, and force re-authentication. This worked until commit `6783582` (Jan 7, 2026, "Reduce duplicate API calls") in the extension repo, which converted `getProspects` from `$.ajax` to `fetch`.

### How it used to work

`getProspects` used `$.ajax`, which rejects its promise on HTTP errors. Since `getProspects` was `async` and the caller (`seekProspects`) discarded the returned promise, the rejection bubbled up as an `unhandledrejection` event. The global handler in `errors.ts` checked `ev.reason.status === 403` (the jqXHR object has `.status`) and called `handleAuthError()`, which clears `chrome.storage.local` and shows the badge.

### How it broke

`fetch` resolves (not rejects) on HTTP errors. The code checks `!res.ok`, logs to console, and returns silently. No rejection occurs, so the global handler never fires. The user stays logged in with a stale token and every subsequent request generates a 403 error.

### Scope

The `$.ajax` calls in `ctas.ts` (update-contact, update-company, update-lead) have `error` callbacks that also swallow 403 — these never triggered the global handler either. `getProspects` was the safety net because it fires on every prospect load, before the user gets a chance to click Save.

## Fix

Two changes in the extension repo (`abstrakt-intelligence-extension`), no changes to the server.

### 1. Fetch wrapper — `src/api.ts` (new file)

```typescript
import { handleAuthError } from './errors';

export async function apiFetch(url: string, options?: RequestInit): Promise<Response> {
    const res = await fetch(url, options);
    if (res.status === 403) {
        handleAuthError();
    }
    return res;
}
```

Replace `fetch` with `apiFetch` in these call sites:

- `src/contacts.ts` line 383 — `getProspects` (the main one that broke)
- `src/refresh.ts` line 42 — `getLastRefresh`
- `src/errors.ts` line 125 — `logErrorToServer/processErrorQueue` (this one logs JS errors to the server; a 403 here means the token is dead, so logout is correct)

Do NOT change `src/bootstrap.js` line 21 (the `/aiq-extension/version` call) — that endpoint doesn't require auth.

### 2. Global jQuery AJAX error handler — `src/abstrakt-intelligence.js`

Add next to the existing `$.ajaxPrefilter` (line 14):

```javascript
$(document).ajaxError(function(_event, jqXHR) {
    if (jqXHR.status === 403) {
        handleAuthError();
    }
});
```

This fires for ALL `$.ajax` failures regardless of per-call `error` callbacks. Covers:

- `src/ctas.ts` — `updateContactIfValid`, `updateCompany`, `updateLead` (PATCH calls)
- `src/refresh.ts` line 11 — `onListRefreshed` (POST to /log, no error callback)
- `src/contacts.ts` lines 47-53 — voicemail contact update (no error callback)
- `src/orum.ts` lines 320, 348 — phone logging (POST to /log)
- `src/issues.ts` — submit-case
- Any future `$.ajax` calls

Import concern: `handleAuthError` is defined in `src/errors.ts` (a TS module), but `abstrakt-intelligence.js` is a plain JS file that currently has no imports from TS modules. Two options:
- Move the `$(document).ajaxError` call to `src/orum.ts` where `handleAuthError` is already importable (it already imports from `errors.ts`). Place it near lines 282-287 where the global error event listeners are registered.
- Or inline the three lines (`chrome.runtime.sendMessage`, `chrome.storage.local.clear()`, `console.log`) directly. This duplicates the logic but keeps it self-contained.

Preferred: add to `src/orum.ts` near the existing global error listener registration.

## Files changed (extension repo)

| File | Change |
|---|---|
| `src/api.ts` | New file — `apiFetch` wrapper |
| `src/contacts.ts` | Import `apiFetch`, replace `fetch` on line 383 |
| `src/refresh.ts` | Import `apiFetch`, replace `fetch` on line 42 |
| `src/errors.ts` | Import `apiFetch`, replace `fetch` on line 125 |
| `src/orum.ts` | Add `$(document).ajaxError` handler near lines 282-287, import `handleAuthError` (already imported) |

## What `handleAuthError` does (no changes needed)

`src/errors.ts` lines 103-107:

```typescript
export function handleAuthError() {
    chrome.runtime.sendMessage({ action: "showBadge" });
    chrome.storage.local.clear();
    console.log('uDab: 403 Unauthorized - logging out user');
}
```

- `showBadge` tells `background.js` to set a red "!" on the extension icon
- `chrome.storage.local.clear()` wipes the stored token, email, and all user data
- Next time the extension checks `isAuthenticated()` or `isUdabReady()`, it returns false and the extension is inert until re-login

## Test plan

### Setup

Both repos running locally, extension loaded unpacked in Chrome, pointed at local server, using real Salesforce credentials.

### Test 1: Token expiry via lowered TTL

1. In the server repo, temporarily change `EXTENSION_AUTH_TTL` in `app/constants/extension.py` from `100000` to `10`
2. Restart the server
3. Log in via the extension popup
4. Wait ~10 seconds
5. Navigate to a page that loads prospects (triggers `getProspects`)
6. **Expected**: extension shows the badge, `chrome.storage.local` is cleared (check via DevTools → Application → Storage), user must re-login
7. **Before the fix**: extension silently fails, shows no badge, user stays "logged in" with broken token

### Test 2: Save operations with expired token

1. Same TTL setup as Test 1
2. Log in, wait ~10 seconds
3. Click Save on a contact/company/lead form (triggers `$.ajax` PATCH)
4. **Expected**: same as Test 1 — badge shown, storage cleared
5. **Before the fix**: generic toastr "error occurred" message, no logout

### Test 3: Normal operation unaffected

1. Restore `EXTENSION_AUTH_TTL` to `100000`
2. Restart server
3. Log in, interact normally — load prospects, save contact, save company, save lead
4. **Expected**: everything works as before, no spurious logouts

### Test 4: Non-auth errors still handled normally

1. With normal TTL, trigger a Salesforce validation error (e.g. leave a required field blank)
2. **Expected**: 400 validation error still shows the swal popup, NOT a logout

### Cleanup

Revert the `EXTENSION_AUTH_TTL` change in the server repo after testing. Do not commit it.
