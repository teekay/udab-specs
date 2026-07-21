# Talk Track Practice Mode (AI Roleplay)

## Problem

SDRs need a way to rehearse calls before dialing. The current talk track view is designed for live calls — it shows the script, the rep clicks through nodes, and session actions are recorded. There is no way to practice.

A third-party vibe-coded app ("Engage Sales System") built on Netlify demonstrates the concept: AI-powered roleplay where Claude plays the prospect, with ElevenLabs providing voice. The client does not expect that app to go to production. Instead, we integrate the intent into the existing talk track system in udab-server.

## Scope

Add a "Practice" mode to the talk track iframe. The rep toggles between live mode (existing behavior, unchanged) and practice mode (AI roleplay). In practice mode:

1. The AI plays the prospect, informed by the same account context the rep sees (qualifiers, differentiators, company details, talk track nodes).
2. The rep speaks or types their pitch. The AI responds in character.
3. Optionally, the rep requests coaching feedback or a grade on their performance.
4. Voice input (STT) and voice output (TTS) are supported via ElevenLabs, proxied through udab-server.

The existing talk track rendering, session tracking, portal buttons, reset logic, and live-call flow are not modified.

## Architecture

```
Extension (content script on orum.com)
  └─ injects <iframe allow="microphone; autoplay"> ← one attribute added
       └─ talk track UI (talk_track.html + talk_track.js)
            │
            │  Practice mode (new):
            ├─ rep types or speaks ──► POST /talk-track/roleplay/chat
            │                           ├─ server assembles system prompt from account + talk track data
            │                           ├─ server calls Claude (streaming)
            │                           └─ SSE stream ◄── tokens back to iframe
            ├─ rep clicks mic ──► MediaRecorder ──► POST /talk-track/roleplay/stt
            │                                        └─ server proxies to ElevenLabs STT
            └─ after AI text reply ──► POST /talk-track/roleplay/tts
                                        ├─ server proxies to ElevenLabs TTS
                                        └─ audio blob ◄── iframe plays via <audio>
```

### Why udab-server mediates everything

- API keys (Anthropic, ElevenLabs) stay server-side. The browser never sees them.
- The server has richer context than the browser: full account details from `build_enriched_account_details()`, all talk track nodes from `_build_questions_from_full_track()`, qualification criteria, differentiators, case studies. It builds the system prompt from authoritative data, not whatever the client happens to have cached.
- Conversation history and practice scores can be stored server-side for analytics (which objections do reps struggle with?).

### Iframe microphone access

The talk track iframe is injected by the Chrome extension in `src/templates.ts` (line ~1227). The extension controls the `<iframe>` tag, so adding `allow="microphone; autoplay"` requires no cooperation from Orum or any host page. The browser prompts the rep for mic permission in the context of orum.com, which is natural for a calling platform.

The pop-out window path (`window.open` triggered by the PiP button in `src/contacts.ts`) gets mic access automatically since pop-outs are top-level browsing contexts.

### Streaming approach: SSE, not WebSockets

SSE (Server-Sent Events) via FastAPI `StreamingResponse` for the Claude text stream. Reasons:

- Works reliably in iframes — no upgrade handshake to worry about with corporate proxies.
- Anthropic's Python SDK natively supports streaming; SSE is the natural transport.
- No persistent connection management. Each roleplay turn is a request/response cycle.
- WebSocket upgrade would add complexity for no latency benefit in a turn-based conversation.

Audio (TTS/STT) uses plain POST request/response — no streaming needed. ElevenLabs TTS for a single reply (~1-3 sentences) returns in ~1-2 seconds; streaming audio adds substantial complexity for marginal gain in a practice context.

## Key Technical Details

### System prompt construction

The server builds the AI prospect's persona from data it already queries:

- **Account details** (`build_enriched_account_details`): company name, industry, services, years in business, location, landmark.
- **Qualifiers** (`SfQualifiedAppointmentSheet`): appointment qualifiers/disqualifiers, target titles, company size criteria.
- **Differentiators**: from the raw SF data (Differentiator fields, name drops, case studies).
- **Talk track nodes**: the full conversation graph. The AI knows what the rep is supposed to say, so it can respond realistically and raise objections that match the talk track's objection nodes.
- **Difficulty level** (optional): easy (cooperative prospect), medium (has objections but persuadable), hard (skeptical, pushes back on everything).

The prompt instructs Claude to stay in character as a prospect who works at the type of company described in the qualifiers, respond naturally to cold call patterns, and raise objections that match the talk track's objection library.

### Conversation state

Each practice session is a sequence of turns stored server-side. Options:

- **Lightweight**: JSON array on an existing or new column on `talk_track_session` (practice sessions are a variant of sessions). Simple, no migration, but not independently queryable.
- **Normalized**: new `talk_track_practice_message` table with `session_id, role, content, created_at`. Queryable for analytics. Slightly more work.

Either way, the conversation history is sent to Claude on each turn so it maintains context within the practice session.

### Voice pipeline

**STT (speech-to-text):**
1. Browser captures audio via `MediaRecorder` API (WebM/Opus codec).
2. Audio blob is POST'd to udab-server as base64 (same pattern as the vibe app).
3. Server forwards to ElevenLabs Scribe v2 (or Whisper — ElevenLabs is what the vibe app uses and works well).
4. Transcribed text is returned to the browser and also fed into the chat endpoint.

**TTS (text-to-speech):**
1. After Claude's text reply is fully streamed, the browser POST's the complete text to the TTS endpoint.
2. Server calls ElevenLabs TTS (Flash v2.5 model, MP3 output).
3. Audio is returned as base64 (same as vibe app) and played via an `<audio>` element.

Voice IDs can be configured per-deployment or selectable by the rep. The vibe app loads the full voice list from ElevenLabs and lets the rep pick — we can start with a single default voice and add selection later.

### Coaching / grading (optional, deferred)

The vibe app has a "Get coaching" button that sends the conversation to Claude with a grading prompt. This is a straightforward extension: same chat endpoint, different system prompt that asks for a score and feedback. Can be added after the core roleplay loop works. If built, scores are stored on the session for analytics.

## What Changes Where

| Component | Change | Size |
|---|---|---|
| **udab-server: routes** | New roleplay endpoints (chat with SSE, STT proxy, TTS proxy) on the talk track router | Medium |
| **udab-server: services** | System prompt builder using existing account/talk-track data; conversation history storage | Medium |
| **udab-server: config** | `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY` in env | Trivial |
| **talk_track.js** | Practice mode toggle, chat thread UI, mic button + MediaRecorder, SSE consumer, audio playback | Medium |
| **talk_track.html** | Mode toggle button in the header area; practice mode container (hidden by default) | Small |
| **talk_track.css** | Chat thread styling, mic button states, mode toggle | Small |
| **Extension: templates.ts** | Add `allow="microphone; autoplay"` to iframe tag | One line |
| **Existing talk track flow** | No changes | — |

## Latency Budget

Rough expected latencies per turn (server-mediated):

| Step | Expected | Notes |
|---|---|---|
| STT (mic → text) | ~500-800ms | ElevenLabs Scribe, depends on audio length |
| Claude first token | ~300-600ms | Streaming via SSE, rep sees tokens arriving |
| Claude full reply | ~1-3s | Typical 1-3 sentence prospect reply |
| TTS (text → audio) | ~1-2s | ElevenLabs Flash model, after full text is available |
| **Total turn** | **~2-4s** | Acceptable for practice; not real-time conversation |

The "prospect thinking" pause between the rep finishing and the AI starting to reply is natural in a cold call context. No optimization needed for v1.

## Out of Scope

- Real-time duplex voice conversation (simultaneous speaking). Turn-based is sufficient for practice.
- Modifying the live-call talk track flow in any way.
- Analytics dashboards for practice scores (data is stored; UI is a separate effort).
- Multi-language support.
- Custom voice cloning (use stock ElevenLabs voices).
