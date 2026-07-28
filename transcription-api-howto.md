# Call Transcription API — Quick HOWTO

Transcribes CloudCall recordings from Salesforce Tasks via Deepgram. You create a job with filters, poll until it's done, then download transcripts from the returned URLs.

**Base URL**: `https://api.abstraktintelligence.com`

**Auth**: every request needs the header `X-AIQ-API-KEY: <your key>`.
Note: an invalid key returns HTTP 200 with `{"unauthorized": "Invalid API Key"}` — check for that key in the body, not just the status code.

## 1. Create a job

```bash
curl -X POST https://api.abstraktintelligence.com/api/transcription-jobs \
  -H 'X-AIQ-API-KEY: <key>' -H 'Content-Type: application/json' \
  -d '{
    "callResult": "appointment",
    "startDate": "2026-07-01",
    "endDate": "2026-07-06",
    "team": "Space Calls"
  }'
```

| Field | Required | Meaning |
|---|---|---|
| `callResult` | yes | `"appointment"` or `"pitch"` — pick deliberately, these are separate workflows |
| `startDate` / `endDate` | yes | Appointment-set date range (dates only, no time) |
| `team` | no | Partner Sales Team name (e.g. `"Space Calls"`) |
| `accountOwnerId` | no | SF User Id — the Account Owner |
| `accountId` | no | SF Account Id |
| `contactId` | no | SF Contact/Lead Id |

Response:

```json
{"success": true, "jobId": 42, "totalTasks": 15, "toTranscribe": 11, "alreadyTranscribed": 3, "skipped": 1}
```

**Max 50 tasks per job.** If you get `"More than 50 tasks match — narrow your filters"`, add `team` or `accountOwnerId`, or shrink the date range. Pitches are high-volume — a single team-day can exceed 50, so per-rep pulls work best there.

The query hits Salesforce live, so brand-new tasks (e.g. today's appointments) are included. Occasionally the POST takes ~20s — that's Salesforce, be patient, use a 60s client timeout.

## 2. Poll for results

```bash
curl https://api.abstraktintelligence.com/api/transcription-jobs/42 \
  -H 'X-AIQ-API-KEY: <key>'
```

While running you get progress counts only. When `"status": "completed"` you also get the task list:

```json
{
  "status": "completed",
  "progress": {"total": 15, "transcribed": 11, "skipped": 4, "failed": 0, "pending": 0},
  "tasks": [
    {"sfTaskId": "00TRj...", "status": "completed", "transcriptUrl": "https://..."},
    {"sfTaskId": "00TRj...", "status": "skipped", "skipReason": "already_transcribed", "transcriptUrl": "https://..."},
    {"sfTaskId": "00TRj...", "status": "failed", "errorMessage": "Deepgram: ..."}
  ]
}
```

A typical job finishes in a few minutes. Poll every 15–30s.

## 3. Download transcripts

`transcriptUrl` is a plain-text file — `curl -o transcript.txt "<url>"` or fetch from your app. No auth header needed on these URLs.

- **URLs expire after 7 days.** Transcripts are stored permanently — just GET the job again for fresh URLs.
- `skipped` + `already_transcribed` means this call was transcribed earlier (possibly in another job) — the URL still works; nothing was re-billed.
- `skipped` + `not_cloudcall_url` means the task's recording isn't a CloudCall recording (e.g. Orum) — those are out of scope.
- Each call is transcribed exactly once, ever — re-running the same filters costs nothing extra for already-done calls.
