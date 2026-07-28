# Call Transcription API — Quick HOWTO

Transcribes call recordings (CloudCall **and Orum**) from Salesforce Tasks via Deepgram. You create a job with filters, poll until it's done, then download transcripts from the returned URLs. There's also a direct lookup by SF Task Id for calls already transcribed.

Transcripts are speaker-labeled and diarized, one line per turn:

```
[00:03] Caller: Hi Susan, this is Mike from ...
[00:11] Prospect: Sure, one second.
[00:15] Automated: Please hold while your call is transferred.
```

`Caller` is the sales rep, `Prospect` the other party, `Automated` an IVR/voicemail voice. Timestamps are elapsed time (minutes can exceed 59 on long calls).

**Base URL**: `https://api.abstraktintelligence.com`

**Auth**: every request needs the header `X-AIQ-API-KEY: <your key>`.
Note: an invalid key returns HTTP 200 with `{"unauthorized": "Invalid API Key"}` — check for that key in the body, not just the status code.

## 1. Create a job

```bash
curl -X POST https://api.abstraktintelligence.com/api/transcription-jobs \
  -H 'X-AIQ-API-KEY: <key>' -H 'Content-Type: application/json' \
  -d '{
    "callResult": "appointment",
    "startDate": "2026-07-26",
    "endDate": "2026-07-28",
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

`"appointment"` matches the known spelling variants of the disposition too (`appt`, `Appoitment`, …), so you don't need to worry about data-entry typos. Follow-up and confirmation calls are **not** included.

Response:

```json
{"success": true, "jobId": 42, "totalTasks": 263, "toTranscribe": 260, "alreadyTranscribed": 2, "skipped": 1}
```

**Max 1000 tasks per job.** If you get `"More than 1000 tasks match — narrow your filters"`, add `team` or `accountOwnerId`, or shrink the date range.

The query hits Salesforce live, so brand-new tasks (e.g. today's appointments) are included. Occasionally the POST takes ~20s — that's Salesforce, be patient, use a 60s client timeout.

**Query recent dates.** Orum recordings expire a few days after the call — a job over last month's dates will return mostly `failed` tasks for Orum calls because the audio no longer exists. Transcribe close to the call date; once transcribed, the transcript is kept forever. (CloudCall recordings last ~30 days.)

## 2. Poll for results

```bash
curl https://api.abstraktintelligence.com/api/transcription-jobs/42 \
  -H 'X-AIQ-API-KEY: <key>'
```

While running you get progress counts only. When `"status": "completed"` you also get the task list:

```json
{
  "status": "completed",
  "progress": {"total": 263, "transcribed": 258, "skipped": 3, "failed": 2, "pending": 0},
  "tasks": [
    {"sfTaskId": "00TRj...", "status": "completed", "transcriptUrl": "https://..."},
    {"sfTaskId": "00TRj...", "status": "skipped", "skipReason": "already_transcribed", "transcriptUrl": "https://..."},
    {"sfTaskId": "00TRj...", "status": "failed", "errorMessage": "Deepgram: ..."}
  ]
}
```

Jobs process ~5 calls in parallel; a few hundred tasks take roughly 10–30 minutes. Poll every 15–30s.

## 3. Download transcripts

`transcriptUrl` is a plain-text file — `curl -o transcript.txt "<url>"` or fetch from your app. No auth header needed on these URLs.

- **URLs expire after 7 days.** Transcripts are stored permanently — just GET the job again (or use the lookup below) for fresh URLs.
- `skipped` + `already_transcribed` means this call was transcribed earlier (possibly in another job) — the URL still works; nothing was re-billed.
- `skipped` + `unsupported_vendor` means the task's recording URL isn't CloudCall or Orum (test rigs, malformed URLs) — those are out of scope.
- `failed` usually means the recording itself was gone (expired) or unreachable; see the date-range note above.
- Each call is transcribed exactly once, ever — re-running the same filters costs nothing extra for already-done calls.

## 4. Look up a transcript by SF Task Id

For calls that have already been transcribed (by any job), you can skip the job machinery entirely:

```bash
curl https://api.abstraktintelligence.com/api/sf-tasks/00TRj00000v6jxlMAA/transcript \
  -H 'X-AIQ-API-KEY: <key>'
```

- **200**: `{"success": true, "sfTaskId": "00TRj...", "transcriptUrl": "https://..."}` — same 7-day presigned URL as above, freshly minted on every call.
- **404**: no transcript. The `error` field says why: never transcribed, transcription currently in progress, or the last attempt failed.

This is a read-only lookup — it never starts a transcription. To get a new call transcribed, create a job (step 1) covering it.
