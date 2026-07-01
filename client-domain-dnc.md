# Feature: Auto-Add Client Domain to Account DNC

## Overview

For Salesforce Accounts with `Status__c` of **"Active"** or **"On Hold"**, extract the domain from the account's `Website` field and add it to that account's DNC (Do Not Contact) list with a new reason of **"Client Domain"**.

Two deliverables:

1. **Backfill**: a CLI command that processes all current Active/On Hold accounts.
2. **Go-forward**: a hook in the existing Salesforce account sync so newly-synced accounts get the same treatment automatically.

---

## Domain Model Reference

### SfAccount (`sf_account` table)

**File**: `app/models/sf_account.py`

Key fields:

| Column | Type | Notes |
|--------|------|-------|
| `sf_id` | `String(18)` | Salesforce ID, unique. This is the FK target for DNC's `account_id`. |
| `Status__c` | `String(80)` | Account status. Filter for values `"Active"` and `"On Hold"`. |
| `Website` | `String(255)` | Free-text website field synced verbatim from Salesforce. Mixed formats (see below). |

The model also defines `ACTIVE_EXCLUSIVITY_STATUSES = ["Active", "Implementing", "Signing", "On Hold"]` — but for this feature we only care about **Active** and **On Hold**.

### DNC (`sp_dne` table)

**File**: `app/models/dnc.py`

The DNC model stores Do Not Contact entries. Key fields for this feature:

| Column | Type | Value for this feature |
|--------|------|----------------------|
| `type` | `String(10)` | `"domain"` (use the constant `DNC.DOMAIN`) |
| `value` | `String(255)` | The extracted domain, e.g. `"example.com"` |
| `account_id` | `String(18)`, FK to `sf_account.sf_id` | The account's `sf_id` |
| `reason` | `String(50)` | `"Client Domain"` (new value) |
| `from_meeting` | `bool` | `False` |
| `ccpa` | `bool` | `False` |

Fields to leave NULL: `client_uuid`, `tenant_id`, `user_uuid`, `details`, `from_exclusivity_client_uuid`.

**Important constraint detail**: The table has `UniqueConstraint("client_uuid", "value", name="client_uuid_value_unique")`. Since we set `client_uuid` to NULL, MySQL allows multiple rows with the same `value` and NULL `client_uuid` — meaning `INSERT IGNORE` will **not** prevent duplicates. You must use an explicit existence check before inserting.

The table does have a composite index `Index("value_account_id", "value", "account_id")` that supports efficient lookups for the dedup check.

### Existing DNC reason values in use

- `"Meeting"` — from `process_devoutcomes.py`
- `"Suspended"` — from `process_suspended.py`
- `"Unsubscribed"` — from `sync_sentiment.py`
- Various DevOutcome values used as-is (e.g. `"Not a Fit"`, `"Client Request"`)
- `"Client Domain"` — **new**, to be added by this feature

The `reason` field is a free-text `String(50)`, not an enum. No migration or picklist update is needed — just use the string `"Client Domain"`.

---

## Website Field Format

`SfAccount.Website` is synced verbatim from Salesforce with no normalization. Real values include:

| Example value | Notes |
|---------------|-------|
| `"https://acme.example"` | Protocol, no trailing slash |
| `"https://www.acme.com/"` | Protocol + www + trailing slash |
| `"https://2hmediagroup.com/"` | Protocol + trailing slash |
| `"http://test.com"` | HTTP protocol |
| `"beta.com"` | Bare domain, no protocol |
| `"sales.com"` | Bare domain |
| `""` (empty string) | Should be skipped |
| `None` | Should be skipped |

### Domain extraction logic

Follow the established pattern from `app/commands/sequencer/integration/contact.py:111`:

```python
cleaned = website.replace('http://', '').replace('https://', '').replace('www.', '').strip('/')
```

Wrap this in a function. Before returning, validate the result looks like a domain (contains at least one `.` and no whitespace). Return `None` for invalid/empty input so the caller can skip.

Suggested location: a small helper function, either at the top of the backfill command file and imported by the sync hook, or in a shared module. Keep it simple — this is three lines of string manipulation, not a library.

---

## Deduplication Strategy

Before inserting a DNC entry, check for an existing row with the same `value` and `account_id`:

```python
existing = await db.execute(
    select(DNC.id)
    .filter(DNC.account_id == account_id, DNC.value == domain)
    .limit(1)
)
if existing.scalars().first():
    # Already exists — skip silently, no logging needed
    continue
```

This matches the pattern in `app/commands/dnc/process_devoutcomes.py:228-235`. The `value_account_id` index makes this lookup efficient.

If the domain is already on the DNC for that account (even with a different reason), skip it. Do not update, do not log, do not panic.

---

## Deliverable 1: Backfill Command

### Location

`app/commands/dnc/backfill_client_domains.py`

### Pattern to follow

Use `app/commands/dnc/backfill_devoutcomes.py` as the structural template — it's the closest analogue (a backfill that creates DNC rows from account-linked data).

### Behavior

1. Query all `sf_account` rows where `Status__c IN ('Active', 'On Hold')` and `Website IS NOT NULL` and `Website != ''`.
2. For each account, extract the domain from `Website`.
3. Skip if domain extraction fails (empty/invalid).
4. Check if a DNC entry already exists for `(value=domain, account_id=sf_id)`. Skip if so.
5. Insert a DNC row with `type="domain"`, `value=domain`, `account_id=sf_id`, `reason="Client Domain"`.
6. Commit after each insert (or batch — follow the pattern).

### CLI interface

```python
async def dnc_backfill_client_domains(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be inserted without writing"),
):
```

### Registration

1. Import in `app/commands/__init__.py`:
   ```python
   from .dnc.backfill_client_domains import dnc_backfill_client_domains
   ```
2. Add to the `commands` list in the same file.

The CLI framework auto-discovers it via `app/cli.py` which iterates `commands` and registers each as a typer command, wrapping async functions with `syncify`.

### Dry-run mode

When `--dry-run` is set, log the domain and account_id that would be inserted, report totals, but write nothing.

---

## Deliverable 2: Go-Forward Hook in Sync

### Location

`app/commands/sfdc/sync.py`, function `process_account_record()` (line 585).

### Hook point

After the existing `await db.commit()` on line 653, add the domain-to-DNC logic. At this point the account record (create or update) has been committed and the `Website` and `Status__c` fields are available on the `sf_account` object.

### Logic

```python
# After the existing commit at line 653:
website = record.get("Website")
status = record.get("Status__c")
if status in ("Active", "On Hold") and website:
    domain = extract_domain(website)  # the shared helper
    if domain:
        existing_dnc = await db.execute(
            select(DNC.id)
            .filter(DNC.account_id == sf_id, DNC.value == domain)
            .limit(1)
        )
        if not existing_dnc.scalars().first():
            db.add(DNC(
                type=DNC.DOMAIN,
                value=domain,
                account_id=sf_id,
                reason="Client Domain",
                from_meeting=False,
                ccpa=False,
            ))
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
```

### What triggers this

- **`sfdc-sync --type=account`** — full account sync, typically run daily or on-demand.
- **`sfdc-stream`** — near-real-time sync, runs every N hours (default 2), picks up recently modified accounts.
- **On-demand sync** — called from `app/services/survey_sync.py` and `app/routes/survey.py` for single-record syncs.

All of these flow through `sfdc_sync_internal()` → `process_account_record()`, so the hook covers all sync paths.

### Import additions in sync.py

At the top of `app/commands/sfdc/sync.py`, add:

```python
from app.models.dnc import DNC
```

`IntegrityError` is already imported (line 10 of sync.py). `select` is already imported (line 36).

---

## What This Feature Does NOT Do

- **Does not remove DNC entries** when an account loses Active/On Hold status. The ticket doesn't ask for this.
- **Does not update the reason** if the domain is already on the DNC with a different reason. If it's there, it's there.
- **Does not stop SmartLead/HeyReach leads** for the domain. That's what `process_devoutcomes` does; this feature is purely about the DNC entry.
- **Does not handle subdomains specially**. `"blog.example.com"` stays as `"blog.example.com"` after stripping protocol/www. If the client wants root-domain extraction (e.g. `tldextract`), that's a separate conversation.

---

## Testing

### Test file

Create `tests/test_dnc_backfill_client_domains.py`. Follow the patterns in `tests/test_sfdc_sync_account.py` and `tests/test_dnc_process_devoutcomes.py`.

### Key test cases

**Domain extraction**:
- `"https://www.example.com/"` → `"example.com"`
- `"http://example.com"` → `"example.com"`
- `"example.com"` → `"example.com"`
- `"www.example.com"` → `"example.com"`
- `""` → `None`
- `None` → `None`
- `"not a domain"` → `None` (no `.`)
- `"https://sub.example.com/path"` → `"sub.example.com"` (path stripped, subdomain preserved)

**Backfill command**:
- Inserts DNC for Active account with a valid website
- Inserts DNC for On Hold account with a valid website
- Skips accounts with other statuses (e.g. "Implementing", "Cancelled")
- Skips accounts with NULL or empty Website
- Skips when DNC entry already exists for the domain+account
- Dry-run mode doesn't insert anything

**Sync hook (process_account_record)**:
- After syncing an Active account with a website, a DNC entry is created
- After syncing an On Hold account with a website, a DNC entry is created
- After syncing a non-Active/On Hold account, no DNC entry is created
- If DNC already exists, no duplicate is created
- If Website is null, no DNC entry is created

### Test infrastructure

The existing test files use `pytest` with `@pytest.mark.asyncio`, mock `AsyncSession` objects (via `unittest.mock.AsyncMock`), and mock the DB execute/commit cycle. Follow the same pattern. See `tests/test_sfdc_sync_account.py` for the mock setup:

```python
@pytest.fixture
def mock_db(self):
    mock = MagicMock()
    mock.execute = AsyncMock()
    mock.add = MagicMock()
    mock.flush = AsyncMock()
    mock.commit = AsyncMock()
    return mock
```

---

## File Summary

| File | Action |
|------|--------|
| `app/commands/dnc/backfill_client_domains.py` | **Create** — backfill command |
| `app/commands/sfdc/sync.py` | **Edit** — add DNC hook after line 653 in `process_account_record()` |
| `app/commands/__init__.py` | **Edit** — add import + registration |
| `tests/test_dnc_backfill_client_domains.py` | **Create** — tests for backfill + domain extraction |
| `tests/test_sfdc_sync_account.py` | **Edit** — add tests for sync hook |

No model changes. No migrations. No new dependencies.

---

## Appendix: Full Source of Key Reference Files

These are the files most relevant to implementation. They are included here so you can work without needing to re-read them.

### `app/commands/sfdc/sync.py` — `process_account_record()` (the hook point)

```python
async def process_account_record(db: AsyncSession, record: Dict[str, Any]):
    sf_id = record.get("Id")
    result = await db.execute(select(SfAccount).filter_by(sf_id=sf_id))
    sf_account = result.unique().scalar_one_or_none()
    if sf_account:
        sf_account.IsDeleted = record.get("IsDeleted")
        sf_account.MasterRecordId = record.get("MasterRecordId")
        sf_account.Name = record.get("Name")
        sf_account.RecordTypeId = record.get("RecordTypeId")
        sf_account.ParentId = record.get("ParentId")
        sf_account.Website = record.get("Website")
        sf_account.Sic = record.get("Sic")
        sf_account.Industry = record.get("Industry")
        sf_account.Source_Industry__c = record.get("Source_Industry__c")
        sf_account.Services_Prospecting_for_Client__c = record.get("Services_Prospecting_for_Client__c")
        sf_account._Services_Prospecting_for_Client__c = clean_services_prospecting(record.get("Services_Prospecting_for_Client__c"))
        sf_account.Services__c = record.get("Services__c")
        sf_account._Services__c = clean_services_picklist(record.get("Services__c"))
        sf_account.Exclusivity__c = record.get("Exclusivity__c")
        sf_account.OwnerId = record.get("OwnerId")
        sf_account.CreatedDate = parse_datetime(record.get("CreatedDate"))
        sf_account.CreatedById = record.get("CreatedById")
        sf_account.LastModifiedDate = parse_datetime(record.get("LastModifiedDate"))
        sf_account.LastModifiedById = record.get("LastModifiedById")
        sf_account.Email__c = record.get("Email__c")
        sf_account.Status__c = record.get("Status__c")
        sf_account.SapperSuite_Client_ID__c = record.get("SapperSuite_Client_ID__c")
    else:
        sf_account = SfAccount(
            sf_id=sf_id,
            IsDeleted=record.get("IsDeleted"),
            MasterRecordId=record.get("MasterRecordId"),
            Name=record.get("Name"),
            RecordTypeId=record.get("RecordTypeId"),
            ParentId=record.get("ParentId"),
            Website=record.get("Website"),
            Sic=record.get("Sic"),
            Industry=record.get("Industry"),
            Source_Industry__c=record.get("Source_Industry__c"),
            Services_Prospecting_for_Client__c=record.get("Services_Prospecting_for_Client__c"),
            _Services_Prospecting_for_Client__c=clean_services_prospecting(record.get("Services_Prospecting_for_Client__c")),
            Services__c=record.get("Services__c"),
            _Services__c=clean_services_picklist(record.get("Services__c")),
            OwnerId=record.get("OwnerId"),
            CreatedDate=parse_datetime(record.get("CreatedDate")),
            CreatedById=record.get("CreatedById"),
            LastModifiedDate=parse_datetime(record.get("LastModifiedDate")),
            LastModifiedById=record.get("LastModifiedById"),
            Email__c=record.get("Email__c"),
            Status__c=record.get("Status__c"),
            SapperSuite_Client_ID__c=record.get("SapperSuite_Client_ID__c"),
            Exclusivity__c=record.get("Exclusivity__c"),
        )
        db.add(sf_account)

    services = clean_services_picklist(record.get("Services__c"))
    await db.flush()
    await db.execute(
        delete(account_services_table).where(
            account_services_table.c.account_id == sf_id
        )
    )
    if services:
        await db.execute(
            insert(account_services_table),
            [{"account_id": sf_id, "service": s} for s in dict.fromkeys(services)],
        )

    await db.commit()
    # <--- INSERT DNC HOOK HERE
```

### `app/commands/dnc/process_devoutcomes.py` — DNC creation pattern (dedup + insert)

The relevant section (lines 228-257):

```python
existing_dne = await db.execute(
    select(DNC.id)
    .filter(DNC.account_id == account_id, DNC.value == dnc_value)
    .limit(1)
)
if existing_dne.scalars().first():
    logger.info(
        f"DNC already exists for account {account_id} / {dnc_value}, skipping"
    )
elif dry_run:
    logger.info(
        f"[DRY RUN] Would add DNC {dnc_type} {dnc_value} for account {account_id}"
    )
else:
    logger.info(f"Adding {dnc_type} {dnc_value} to account DNC")
    dne = DNC(
        type=dnc_type,
        value=dnc_value,
        account_id=account_id,
        reason=reason,
        from_meeting=is_appointment,
        ccpa=False,
        details=details,
    )
    db.add(dne)
    try:
        await db.commit()
        stats["dnc_added"] += 1
    except IntegrityError:
        await db.rollback()
```

### `app/commands/__init__.py` — Command registration

Imports go at the top with the other DNC imports (around line 6-9):

```python
from .dnc.process_devoutcomes import dnc_process_devoutcomes
from .dnc.backfill_devoutcomes import dnc_backfill_devoutcomes
from .dnc.sync_account_ids import dnc_sync_account_ids
from .dnc.process_suspended import dnc_process_suspended
# Add: from .dnc.backfill_client_domains import dnc_backfill_client_domains
```

Then add `dnc_backfill_client_domains` to the `commands` list (around line 85-87, near the other DNC commands):

```python
dnc_process_devoutcomes,
dnc_backfill_devoutcomes,
dnc_process_suspended,
# Add: dnc_backfill_client_domains,
```

### `tests/test_sfdc_sync_account.py` — Test fixture for sync tests

The `ACCOUNT_RECORD` fixture and mock_db setup:

```python
ACCOUNT_RECORD = {
    "Id": "001AAA000000001",
    "IsDeleted": False,
    "MasterRecordId": None,
    "Name": "Acme Corp",
    "RecordTypeId": "0124w000001YqDwAAK",
    "ParentId": None,
    "Website": "https://acme.example",
    "Sic": None,
    "Industry": "Technology",
    "Source_Industry__c": "SaaS",
    "Services_Prospecting_for_Client__c": None,
    "Services__c": "Web Design;SEO;PPC",
    "Exclusivity__c": None,
    "OwnerId": "005000000000001",
    "CreatedDate": "2026-01-15T14:25:00.000+0000",
    "CreatedById": "005000000000001",
    "LastModifiedDate": "2026-01-15T14:30:00.000+0000",
    "LastModifiedById": "005000000000001",
    "Email__c": "info@acme.example",
    "Status__c": "Active",
    "SapperSuite_Client_ID__c": None,
}

@pytest.fixture
def mock_db(self):
    mock = MagicMock()
    mock.execute = AsyncMock()
    mock.add = MagicMock()
    mock.flush = AsyncMock()
    mock.commit = AsyncMock()
    return mock
```
