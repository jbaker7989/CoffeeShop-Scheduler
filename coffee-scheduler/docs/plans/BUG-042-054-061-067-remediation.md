# BUG-042 / BUG-054 / BUG-061 / BUG-067 — Remediation Plan

**Status:** Ready for execution
**Branch base:** `bug-066-forecast-payload` @ `6c18cc1` (main is an ancestor; `main` does **not** yet contain migration `0021`)
**Database:** PostgreSQL 16.15, `coffeeshop` on `127.0.0.1:5432`, alembic at `0021`
**Executor:** Codex sub-agents (`codex-exec-writer`), one branch per bug
**Verification:** `pytest` against the shared DB; migration round-trip runs serially (it wipes the DB)

---

## 0. Ground rules for every bug

1. **Test first.** Write the failing test, confirm it fails for the stated reason, then implement.
2. **One branch per bug.** `fix/bug-042-audit-outage`, `fix/bug-054-pagination`, `fix/bug-061-store-hours`, `fix/bug-067-migration-lock`.
3. **Do not touch unrelated files.** No reformatting, no drive-by fixes.
4. **Tag tests** with `# spec: <feature>.acN` where the bug names an AC; otherwise `# bug: BUG-0NN`.
5. **Run only your own test file** plus the pre-existing tests you could plausibly break. Do not run `test_migrations_roundtrip.py` in parallel with anything (it drops the schema).
6. **Commit message:** `fix(BUG-0NN): <one-line summary>` with a body naming the ACs and the test that proves it.
7. **Push** the branch to `origin` after the tests pass.
8. **Never** loosen RLS, drop a constraint, or bypass a guard to make a test pass.

---

## 1. BUG-042 — audit-store outage turns a successful login into a 500

**File:** `coffee-scheduler/auth/service.py`
**Test:** `coffee-scheduler/test/test_f61_audit_contract.py`
**Priority:** P2 · **Size:** S

### Root cause
`TenantAuthService._audit` logs and **re-raises** on any audit-write failure. It is called on the login
success path (`auth.login.succeeded`), so a transient `auth_record_audit` failure destroys an otherwise
valid authentication: the caller gets a 500 and no tokens. Availability coupling — fail-closed on a
non-security-critical event.

### Required behaviour
| Audit event | On audit-store failure |
|---|---|
| `auth.login.succeeded` | **log + metric, continue returning tokens** |
| `auth.login.failed` | log + metric, continue raising the original auth error |
| `invitation.created` | **fail closed** (privilege grant must be traced) |
| everything else | fail closed (current behaviour) |

### Acceptance criteria
- **AC1** A login whose credentials are valid succeeds (returns tokens) when `record_audit` raises.
- **AC2** The failure is surfaced: one structured `ERROR`-level log record with a stable key
  (`audit.write.failed`) and an incrementing in-process counter (`auth.service.audit_failure_count()`).
- **AC3** The failure is **not** silently swallowed — the counter is non-zero and the log record exists.
- **AC4** `invitation.created` still raises when the audit write fails (fail-closed preserved).
- **AC5** `auth.login.failed` still surfaces the original `AuthenticationError`, not the audit error.
- **AC6** The existing `test_audit_failure_is_surfaced` is updated to assert the new contract, not deleted.

### Design
```python
# module level
_AUDIT_FAILURES: dict[str, int] = {}
def audit_failure_count(action: str | None = None) -> int: ...

class AuditPolicy(Enum):
    BEST_EFFORT = "best_effort"   # log + metric, continue
    REQUIRED = "required"          # log + metric, re-raise

def _audit(self, entry, *, policy=AuditPolicy.REQUIRED) -> None:
    try:
        self._repo.record_audit(entry)
    except Exception:
        _AUDIT_FAILURES[entry.action] = _AUDIT_FAILURES.get(entry.action, 0) + 1
        logging.getLogger(__name__).error(
            "audit.write.failed", extra={"action": entry.action, "policy": policy.value},
            exc_info=True,
        )
        if policy is AuditPolicy.REQUIRED:
            raise
```
Call sites: `auth.login.succeeded` → `BEST_EFFORT`; `auth.login.failed` → `BEST_EFFORT` but re-raise the
*auth* error (the `except` block already re-raises; the audit call must not mask it — wrap in try/except or
call with `BEST_EFFORT`); `invitation.created` → `REQUIRED` (default).

### Tests to add (in `test_f61_audit_contract.py`)
| Test | Asserts |
|---|---|
| `test_login_succeeds_when_audit_store_is_down` | tokens returned; counter incremented; ERROR log captured via `assertLogs` |
| `test_login_failure_keeps_original_error_when_audit_down` | `AuthenticationError` raised, not the audit error |
| `test_invitation_creation_fails_closed_when_audit_down` | raises |
| `test_audit_failure_counter_accumulates` | two failures → count 2 |

### Edge cases
- Audit raises `BaseException` subclass (`KeyboardInterrupt`) → must **not** be caught. Catch `Exception` only.
- Audit raises on the *failed-login* path → the original auth error must survive.
- Concurrent logins → counter increments atomically enough for a test (use a lock or `dict` + GIL; document).
- Audit raises a non-`Exception` (`SystemExit`) → propagates.

### Verify
```
cd coffee-scheduler && DATABASE_URL=... python3 -m pytest test/test_f61_audit_contract.py -q
```

---

## 2. BUG-054 — `Repository.list` accepts negative/unbounded pagination

**File:** `coffee-scheduler/data_access/repository.py`
**Test:** `coffee-scheduler/test/test_data_access.py`
**Priority:** P2 · **Size:** XS

### Root cause
`list(limit=..., offset=...)` does `LIMIT %s OFFSET %s` with `int()` coercion only. `LIMIT -1` in
PostgreSQL means *no limit*; `offset=-5` is an error; `int("100")` and `int(100.9)` silently coerce lossily;
`int(True) == 1` so a bool is accepted as 1.

### Acceptance criteria
- **AC1** `limit` must be an `int` (not `bool`), `1 <= limit <= 1000`; default `100`.
- **AC2** `offset` must be an `int` (not `bool`), `offset >= 0`; default `0`.
- **AC3** Invalid input raises `ValueError` with a message naming the parameter, the value and the bound —
  **before** any cursor is touched.
- **AC4** Valid input binds as parameters and keeps the existing ordering/filter behaviour.

### Design
```python
MAX_LIMIT = 1000

def _bounded_int(name: str, value: object, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {type(value).__name__}: {value!r}")
    if value < minimum or (maximum is not None and value > maximum):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{name} must be {bound}, got {value!r}")
    return value
```
Call at the very top of `list()`, before `_ident`/SQL construction. Keep `int()` out of the bind path.

### Tests to add
| Test | Asserts |
|---|---|
| `test_list_rejects_negative_limit` | `ValueError` |
| `test_list_rejects_zero_limit` | `ValueError` |
| `test_list_rejects_limit_above_max` | `ValueError` at 1001 |
| `test_list_accepts_limit_at_max` | 1000 ok |
| `test_list_rejects_negative_offset` | `ValueError` |
| `test_list_rejects_bool_and_float_and_str` | `ValueError` for `True`, `1.5`, `"10"` |
| `test_list_rejects_before_touching_cursor` | fake cursor that raises on any attribute access; validation must raise first |
| `test_list_binds_valid_pagination` | SQL params contain the ints |

### Edge cases
- `numpy.int64` / `Decimal` → rejected (not `int`). Document.
- `limit=0` → rejected (zero rows is `offset`-only, not a valid page size).
- Enormous `10**9` → rejected by the max bound.
- `offset` has no upper bound (pagination through a large table is legitimate).
- Existing callers: grep for `.list(` and confirm none pass a bool/float/str.

### Verify
```
cd coffee-scheduler && DATABASE_URL=... python3 -m pytest test/test_data_access.py -q
```

---

## 3. BUG-061 — `load_store_hours` collapses per-weekday hours and floors open minutes

**File:** `coffee-scheduler/contracts/forecast.py`
**Test:** `coffee-scheduler/test/test_forecast_contract.py`
**Priority:** P2 · **Size:** S

### Root cause
Two defects in one function:
1. `grouped.setdefault(location_id, StoreOpenWindow(...))` keeps the **first** row's `open_hour`/`close_hour`
   for the whole store and only unions the weekday set. A store open 06:00–21:00 Mon–Fri but 07:00–18:00
   Sat/Sun is validated against whichever row came first.
2. `open_hour = open_time.hour` **floors** the minutes, while `close_hour` **ceils** them. A 06:30 open
   accepts a 06:00 forecast.

### Acceptance criteria
- **AC1** `load_store_hours` returns per-`(location, weekday)` windows; a store with differing weekday hours
  validates each day against its own window.
- **AC2** Open minutes are ceiled like close minutes: `06:30` → first valid hour is `07`.
- **AC3** `validate_set` / `validate_record` resolve the window for the record's **local weekday**, not the
  store's uniform window.
- **AC4** Backward compatible: a plain `Mapping[int, StoreOpenWindow]` (uniform window per store) still works.
- **AC5** `docs/contracts/forecast.md` documents the rounding rule and the per-weekday model.

### Design
```python
# New type
StoreWeekHours = Mapping[int, Mapping[int, StoreOpenWindow]]   # location -> weekday -> window

def _window_for(store_hours, location: int, weekday: int) -> StoreOpenWindow:
    """Accept both the uniform per-store and the per-weekday mapping."""
    by_day = store_hours.get(location)
    if by_day is None: raise ...
    if isinstance(next(iter(by_day)), StoreOpenWindow):  # uniform
        return by_day
    return by_day.get(weekday) or ...  # closed day
```
`load_store_hours` returns `dict[int, dict[int, StoreOpenWindow]]`.
`validate_set` keeps `store_hours: Mapping[int, StoreOpenWindow | Mapping[int, StoreOpenWindow]]` and
resolves via `_window_for(...)` using `record.forecast_hour.astimezone(ZoneInfo("America/New_York")).weekday()`.
Rounding: `open_hour = open_time.hour + (1 if open_time.minute else 0)`; same for close.

### Tests to add
| Test | Asserts |
|---|---|
| `test_load_store_hours_keeps_per_weekday_windows` | DB-gated: two rows with different hours produce two distinct windows |
| `test_load_store_hours_ceils_open_minutes` | 06:30 open → 7 |
| `test_validate_set_uses_record_weekday_window` | Saturday 06:00 forecast rejected when Saturday opens 07:00 |
| `test_validate_set_accepts_uniform_window_mapping` | backward compatibility |
| `test_validate_set_weekday_boundary_across_utc` | a UTC Sunday 02:00 record maps to Saturday 21:00 local → uses Saturday's window |

### Edge cases
- A weekday with **no** row → store closed that day → every forecast for it rejected.
- `close_hour <= open_hour` (overnight) → reject at load time with a clear error (do not silently accept).
- Duplicate `(location, weekday)` rows → last wins; document (or raise).
- `open_time` exactly on the hour → unchanged.
- DST transition days → weekday resolution uses the local calendar date, not UTC.
- `open_weekdays` on the uniform type still honoured (a window whose weekday set excludes the day → closed).

### Verify
```
cd coffee-scheduler && DATABASE_URL=... python3 -m pytest test/test_forecast_contract.py -q
```

---

## 4. BUG-067 — migration `0021` is unavailable while an ordinary forecast read runs

**File:** `coffee-scheduler/db/migrations/versions/0021_bug065_forecast_finite_interval.py`
**Test:** new `coffee-scheduler/test/test_bug067_migration_lock.py` (DB-gated)
**Priority:** P2 · **Size:** M

### Root cause
The migration takes `LOCK TABLE forecasts IN ACCESS EXCLUSIVE MODE` and holds it through the preflight
`SELECT` **and** the `ADD CONSTRAINT` table scan. `ACCESS EXCLUSIVE` conflicts with `ACCESS SHARE`, so any
open `SELECT` blocks the upgrade for its full duration; with `lock_timeout = 5s` the upgrade fails after 5s.

### Required behaviour
- Keep the outcome: a newly inserted non-finite `lower`/`p70`/`upper` is rejected by
  `forecasts_forecast_finite`.
- Keep the actionable preflight diagnostic for legacy rows; do not rewrite rows.
- **Shorten the exclusive window to the metadata phase only.**

### Design
Replace the up-front `LOCK TABLE` + `ADD CONSTRAINT` (which validates inline) with:
```sql
SET LOCAL lock_timeout = '5s';
-- 1. actionable preflight (plain SELECT, ACCESS SHARE, no exclusive lock)
DO $$ ... RAISE EXCEPTION 'BUG-065 preflight: ...' ... $$;
-- 2. enforce future writes only; brief metadata lock, no table scan
ALTER TABLE forecasts ADD CONSTRAINT forecasts_forecast_finite
  CHECK (lower <> 'NaN' AND ... ) NOT VALID;
-- 3. validate existing rows under SHARE UPDATE EXCLUSIVE (reads allowed)
SET LOCAL lock_timeout = '0';   -- validation may be long; do not fail on it
ALTER TABLE forecasts VALIDATE CONSTRAINT forecasts_forecast_finite;
```
Notes:
- `NOT VALID` still takes `ACCESS EXCLUSIVE` briefly — but only for the catalog update, not the scan.
- `VALIDATE CONSTRAINT` takes `SHARE UPDATE EXCLUSIVE`: blocks writes, **allows reads**.
- The preflight→enforce race is narrowed but not eliminated; a concurrent non-finite insert between the
  preflight and the `ADD CONSTRAINT` fails with the constraint error rather than the friendly message.
  Document that in the migration docstring and keep the preflight as the primary diagnostic.
- Downgrade drops the constraint (`DROP CONSTRAINT IF EXISTS`).

### Tests (`test_bug067_migration_lock.py`, `@unittest.skipUnless(DATABASE_URL)`)
| Test | Asserts |
|---|---|
| `test_enforcement_installed_before_validation` | after upgrade, `convalidated` is true and a non-finite insert raises `CheckViolation` naming the constraint |
| `test_upgrade_fails_fast_behind_long_reader` | connection A holds `BEGIN; SELECT ...; pg_sleep(6)`; upgrade exits non-zero within ~6s with a lock-timeout message; after A commits, a retry succeeds |
| `test_validation_does_not_block_reads` | while `VALIDATE CONSTRAINT` runs on a seeded table, a concurrent `SELECT` succeeds |
| `test_preflight_rejects_legacy_non_finite_row` | seed a non-finite row with the constraint absent → upgrade fails with `BUG-065 preflight`; row unchanged |
| `test_downgrade_drops_constraint` | after downgrade, the constraint is gone and non-finite inserts are accepted |

The concurrency tests need a **separate database** (they downgrade/upgrade). Use
`DATABASE_URL_BUG067` if set, else skip with a clear message. Do **not** point them at the shared DB.

### Edge cases
- `lock_timeout` set to `0` for validation — a very long validation blocks writes but not reads; document.
- Constraint name collision on re-run → `ADD CONSTRAINT IF NOT EXISTS` is not valid in PG16; use a
  `DO $$ ... IF NOT EXISTS (SELECT FROM pg_constraint ...) ... $$` guard.
- Empty `forecasts` table → validation is instant; still must succeed.
- Downgrade when the constraint was never created → `DROP CONSTRAINT IF EXISTS`.
- A second `upgrade` after a partial failure → idempotent.

### Verify
```
cd coffee-scheduler && DATABASE_URL_BUG067=... python3 -m pytest test/test_bug067_migration_lock.py -q
```

---

## 5. Orchestration sequence

| Step | Actor | Output |
|---|---|---|
| 1 | Codex writer ×4, one branch each | failing test → code → passing test |
| 2 | Codex writer | `git commit`, `git push -u origin <branch>` |
| 3 | Codex reviewer (`codex-exec`, read-only) per branch | resultant-bug + edge-case report |
| 4 | Parent | file new stories in `Backlog.md` for each confirmed finding |
| 5 | Parent | merge order: BUG-054 → BUG-042 → BUG-061 → BUG-067 (BUG-067 last; it touches migrations) |

### Merge order rationale
BUG-054 and BUG-042 touch disjoint modules. BUG-061 changes a public type in `contracts/forecast.py`, so it
lands after the two independent fixes. BUG-067 rewrites a migration that a deployed database may already
have applied — it lands last and needs a `down_revision` check before merge.

### Rollback
Every branch is independently revertible. BUG-067 additionally needs a `downgrade()` that removes only the
constraint, so `alembic downgrade -1` restores the pre-fix state.

---

## 6. Definition of done

- [ ] Four branches pushed with green tests for their own file.
- [ ] `test_migrations_roundtrip.py` green **serially** after BUG-067 lands.
- [ ] Adversarial review completed per branch; confirmed findings filed as new BUG-0NN stories.
- [ ] No unrelated file modified (`git diff --stat` reviewed per branch).
- [ ] Backlog statuses updated for the four bugs.
