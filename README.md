# uDab specs

Specs, how-tos, research notes and handoff reports for the uDab platform, one folder per area. Every file carries frontmatter: `kind` (spec | howto | notes | handoff), `status` (draft = analysis, not approved; ready = approved, not built; in-progress = partially built; done = built and merged; superseded = a later doc changed the approach, see `superseded_by`; dropped = never built, abandoned), `area`, `updated` (date of the last status change), `repos`, `summary`.
This file is generated — do not edit by hand; run `python3 scripts/index.py` from `udab-specs/` after changing any frontmatter.
Reading rule: open the area's `NOTES.md` first, then only active specs (draft/ready/in-progress) for your area; don't open done/superseded docs unless doing archaeology. Areas without a `NOTES.md` gain one when their first living reference is needed.

## Transcription

- **Living reference:** [transcription/NOTES.md](transcription/NOTES.md) — read this first.

**Done**

| doc | kind | status | updated | summary |
|---|---|---|---|---|
| [spend-report.md](transcription/spend-report.md) | spec | done | 2026-08-28 | Estimated Deepgram cost per run from sf_task durations, summary matrix, runs/tasks CSV exports (Transcriptions tab). |
| [auto.md](transcription/auto.md) | spec | done | 2026-08-24 | auto-transcribe poller: per-minute scan of new Pipeline Client/Active Tasks, single-flight worker, claimed-exclusion. |
| [v2.md](transcription/v2.md) | spec | done | 2026-08-03 | Milestone 1: diarized Caller/Prospect/Automated transcripts, disposition variant list, Orum recordings via ?raw=true. |
| [cloudcall-url-stamper.md](transcription/cloudcall-url-stamper.md) | spec | done | 2026-08-03 | stamp-cloudcall-urls: per-minute job resolving CloudCall recording URLs (SessionID + Leg==1) and PATCHing SF Tasks. |
| [cloudcall-api-notes.md](transcription/cloudcall-api-notes.md) | notes | done | 2026-07-30 | Reverse-engineered CloudCall ng-api auth and calls listing, with verified corrections (no leg=c, SessionID is the key). |
| [read-view.md](transcription/read-view.md) | spec | done | 2026-07-08 | Read-only Transcriptions tab on the Calls page: job list, task drill-down, shared TranscriptViewer, JWT list endpoints. |
| [deepgram-key-provisioning.md](transcription/deepgram-key-provisioning.md) | spec | done | 2026-06-14 | Server-minted short-lived Deepgram tokens for the native app, per-user stt_access/stt_model on sp_extension_version. |

<details>
<summary>Superseded / dropped</summary>

- [cloudcall-transcription.md](transcription/cloudcall-transcription.md) (spec, superseded, 2026-07-27) — superseded by [transcription/v2.md](transcription/v2.md): Original bulk transcription API: job tables, POST/GET /api/transcription-jobs, Deepgram by URL, S3 .txt, Batch worker.

</details>

## Appointment emails

**Active**

| doc | kind | status | updated | summary |
|---|---|---|---|---|
| [call-queue.md](appointment-emails/call-queue.md) | spec | draft | 2026-09-01 | Analysis for an account-manager queue of transcribed appointment calls; needs audio archiving and client answers Q1-Q17. |

## ZoomInfo exit

**Active**

| doc | kind | status | updated | summary |
|---|---|---|---|---|
| [archive.md](zoominfo-exit/archive.md) | spec | in-progress | 2026-08-21 | Umbrella: archive ZoomInfo-only prospects and delete SF contacts; decisions, keep rules, slices, open client questions. |

**Done**

| doc | kind | status | updated | summary |
|---|---|---|---|---|
| [dump-delete-list.md](zoominfo-exit/dump-delete-list.md) | spec | done | 2026-08-24 | --dry-run --dump-delete-list: per-shard delete-candidate CSV for an admin Data Loader delete, plus a merge script. |
| [delete-test-batch.md](zoominfo-exit/delete-test-batch.md) | spec | done | 2026-08-21 | --delete-contacts --max-deletes N: capped live SF contact deletion, provenance columns on sf_contact, deletions CSV. |
| [slice0-dev.md](zoominfo-exit/slice0-dev.md) | spec | done | 2026-07-21 | Slice 0 dev spec: 5x5 email matcher, decision engine + reason codes, dry-run CLI and reports, legacy pipeline teardown. |

## Extension

**Done**

| doc | kind | status | updated | summary |
|---|---|---|---|---|
| [token-reset-and-embed-auth.md](extension/token-reset-and-embed-auth.md) | handoff | done | 2026-08-17 | Admin sp_user_token reset button, and why blank/deleted token rows cause silent Error 1013 in embeds with no 403/logout. |
| [403-auth-fix.md](extension/403-auth-fix.md) | spec | done | 2026-07-03 | apiFetch wrapper and global $.ajaxError handler so any 403 clears the extension session and forces re-login. |

## Talk track

**Active**

| doc | kind | status | updated | summary |
|---|---|---|---|---|
| [account-filters.md](talk-track/account-filters.md) | spec | in-progress | 2026-08-21 | Iteration 1 shipped (exact account autocomplete on the Talk Track list); record type/status filters and roll-up open. |
| [practice-mode.md](talk-track/practice-mode.md) | spec | draft | 2026-07-17 | Proposal: AI roleplay practice mode in the talk track iframe via server-mediated Claude SSE and ElevenLabs STT/TTS. |

**Done**

| doc | kind | status | updated | summary |
|---|---|---|---|---|
| [sf-task-session-link-report.md](talk-track/sf-task-session-link-report.md) | spec | done | 2026-08-02 | Per-record unmatched report (detail + summary CSV to S3) with diagnosed reasons for the session-link job, both sides. |
| [sf-task-session-link.md](talk-track/sf-task-session-link.md) | spec | done | 2026-07-12 | Daily job stamping Talk_Track_Session_Id__c on SF call Tasks by dialed number + contact + 1h window. |

## DNC

**Done**

| doc | kind | status | updated | summary |
|---|---|---|---|---|
| [client-domain-dnc.md](dnc/client-domain-dnc.md) | spec | done | 2026-07-02 | Add each Active/On Hold account's website domain to its DNC list (reason 'Client Domain'): backfill command + sync hook. |

## Infra

**Done**

| doc | kind | status | updated | summary |
|---|---|---|---|---|
| [ci-database-and-asyncio-fixes.md](infra/ci-database-and-asyncio-fixes.md) | spec | done | 2026-07-17 | CI gets a fresh schema-only MySQL sidecar; the Docker image copies pytest.ini so async tests no longer silently skip. |
