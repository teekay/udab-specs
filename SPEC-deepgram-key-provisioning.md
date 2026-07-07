# Server-Provisioned Deepgram Keys

## Problem

The native transcription app (udab-moonshine-poc) currently requires users to manually enter a long-lived Deepgram API key, stored in a local config file. This is unsuitable for a sales floor deployment:

- Keys can't be revoked per-user without changing the shared key
- No control over who uses cloud STT vs local Moonshine
- API key is exposed on the client machine indefinitely

## Solution

The server (udab-server) provisions short-lived Deepgram tokens on demand. The Chrome extension proxies these tokens to the native app at call start. Per-user access to cloud STT is gated via a new column on `sp_extension_version`.

## Architecture

### Current flow

```
Extension ──startCall──► Native App (reads Deepgram key from local config.json)
                              │
                              └──► Deepgram WebSocket (long-lived key)
```

### New flow

```
Extension ──GET /call-settings──► udab-server (checks stt_access for user)
    │                                  │
    │◄── { sttEngine: "deepgram:nova-3" } ──┘
    │
    │  [call starts]
    │
    ├──POST /deepgram-token──► udab-server
    │                              │
    │                              ├──► Deepgram /v1/auth/token (TTL: 30s)
    │◄── { token: "ey..." } ──────┘
    │
    └──startCall { engine, token }──► Native App
                                          │
                                          └──► Deepgram WebSocket (token expires after handshake — OK)
```

## Design Decisions

### Token strategy: Deepgram Token-Based Auth (JWT)

Use Deepgram's `/v1/auth/token` endpoint rather than minting full API keys.

- Returns a short-lived JWT scoped to `usage:write`
- Default TTL: 30 seconds, max: 3600 seconds
- Token is only validated at WebSocket handshake; the connection stays open after expiry
- 30s TTL is sufficient — token only needs to survive the few seconds between issuance and the native app connecting

Ref: https://developers.deepgram.com/guides/fundamentals/token-based-authentication

### Per-user gating: `stt_access` column on `sp_extension_version`

Add a column to the existing `sp_extension_version` table rather than creating a new table. This table already maps `email → per-user config`.

- `stt_access`: `"local"` (default) or `"cloud"`
- Users with `"local"` get Moonshine only — no token minted, no Deepgram option
- Users with `"cloud"` get the server-dictated engine and a provisioned token

### Model selection: server-dictated, per-user

The server decides which STT engine/model each user gets. The sales rep never sees or picks a model. The native app receives the engine identifier in the `startCall` message and uses it.

A new `stt_model` column on `sp_extension_version` (e.g. `"nova-3"`, `"nova-2"`) controls which Deepgram model a cloud-enabled user gets. This supports A/B comparison across the sales floor — different reps can run different models simultaneously. The admin UI exposes this per-user.

Default value: `"nova-3"`. Only meaningful when `stt_access = "cloud"` — ignored for local users.

Once the team converges on a single model, the column remains but every row will have the same value. No migration needed to simplify later.

### Revoking access

Flip `stt_access` from `"cloud"` to `"local"`. The next call will not receive a Deepgram token. Any in-progress call continues (WebSocket already open) but the next one falls back to Moonshine.

### Two operating modes in the native app

**Server-triggered mode** (via extension `startCall`): the server dictates the engine. When the engine is `"moonshine"`, the app uses `medium-streaming-en` (the largest/best local model) — no user choice. When the engine is `"deepgram:<model>"`, the app uses the provided token and model. The transcriber dropdown is not shown.

**On-demand mode** (user clicks Record in the app UI): full local model selection remains — user can pick between tiny, small, and medium Moonshine variants via the dropdown. Deepgram is not available in this mode. The `ApiKeyDialog` and local `config.json` key storage will be removed.

## Changes Per Repo

### udab-server

1. **Migration**: add two columns to `sp_extension_version`:
   - `stt_access VARCHAR(10) NOT NULL DEFAULT 'local'` — `"local"` or `"cloud"`
   - `stt_model VARCHAR(20) NOT NULL DEFAULT 'nova-3'` — Deepgram model identifier (only used when `stt_access = "cloud"`)
2. **Config**: store the master Deepgram API key in environment / secrets (never exposed to clients)
3. **Endpoint — `GET /call-settings`**: extend the existing response to include `sttEngine` based on the user's `stt_access` and `stt_model`:
   - `stt_access = "local"` → `{ sttEngine: "moonshine" }`
   - `stt_access = "cloud"` → `{ sttEngine: "deepgram:nova-3" }` (model from user's `stt_model` column)
4. **Endpoint — `POST /deepgram-token`** (new): authenticated, checks `stt_access == "cloud"`, calls Deepgram `/v1/auth/token` with 30s TTL, returns the JWT
5. **Schemas/routes for `sp_extension_version`**: extend create/update to accept `stt_access` and `stt_model`

### abstrakt-intelligence-extension

1. **On Orum page load**: already calls `/call-settings` — store the returned `sttEngine`
2. **On call start**: if `sttEngine` starts with `"deepgram:"`, call `/deepgram-token` to get a short-lived token
3. **`startCall` native message**: add `engine` and `apiKey` fields to the payload:
   ```json
   {
     "command": "startCall",
     "engine": "deepgram:nova-3",
     "apiKey": "ey...",
     "rep": { ... },
     "prospect": { ... },
     ...
   }
   ```
   When engine is `"moonshine"`, `apiKey` is omitted.

### udab-moonshine-poc (native app)

1. **`PipeListenerService.HandleStartCall`**: read `engine` and `apiKey` from the incoming message
2. **Engine selection**: if `engine` starts with `"deepgram:"`, use `DeepgramTranscriptionService` with the provided key; otherwise use `TranscriptionService` (Moonshine)
3. **Remove**: `ApiKeyDialog`, `AppConfig.GetDeepgramApiKey` / `SetDeepgramApiKey`, local config file key storage
4. **`AvailableTranscribers` dropdown**: hide or remove Deepgram options from the UI — model is server-dictated
5. **Standalone mode**: always uses Moonshine, no Deepgram option

### udab-client (admin UI)

1. **Extension Version screen → Version tab**: add per-user controls:
   - `STT Access` dropdown: Local / Cloud
   - `STT Model` dropdown: nova-3 / nova-2 / etc. (visible only when access = Cloud)
2. **API calls**: use the existing `PUT /extension/version/{id}` endpoint (extended to accept `stt_access` and `stt_model`)

## Decisions — Error Handling

Fail fast and loud, no silent fallback. If the server says `cloud` but the token fetch fails, the native app should surface an error and not start transcription. This ensures misconfigurations are caught immediately rather than silently degrading to a different engine.

## Open Questions

- [ ] Rate limiting on `/deepgram-token` — probably per-user, max N tokens per minute?
- [ ] Should `/call-settings` cache the `stt_access` lookup, or is it lightweight enough to query each time?
