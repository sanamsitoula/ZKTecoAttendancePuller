# deno2 → ZKTecePuller ERP Integration Plan

Status: **Planning only — nothing in this document has been built yet.** This is Phase 1 of turning the
current attendance/payroll system into a full ERP for the organization by absorbing `deno2`
(a Postgres database called `press_jemc` — the org's printing-press production system: job tickets,
plates/forma, denomination stock, vehicle fleet, fuel, reconciliation).

Source dump audited: `D:\claude_project\deno2\sql\deno2_20260722.sql` (pg_dump custom format,
Postgres 17.5, 65 tables, ~30 functions/triggers, several views). All findings below marked
**(verified against real data)** were confirmed by restoring the dump into a throwaway local scratch
database, inspecting it read-only, and dropping it afterward — not guessed from schema alone.

## 0. Decisions confirmed with you

1. **User identity is unified, not duplicated.** deno2's `users` table is merged into our existing
   `web_users` table; every FK that pointed at `users(id)` across all migrated tables is remapped to
   `web_users.id`.
2. **Real historical data is migrated**, not just schema.
3. **Phase 1 stands up two modules**: **Fleet / Vehicle Management** and **Press Production**
   (Job Ticket → Forma → Deno → D2M → Book Packing). Reconciliation follows in a later phase.
4. **HR / Payroll / Attendance are excluded** — that system already exists here.
5. **Role model is extended now**, not collapsed — deno2's real production roles (operator, incharge,
   supervisor, marketing, press) become real values in `web_users.role` from day one (§3.5).
6. **Fiscal years are unified system-wide** — one single `fiscal_years` table/standard used by
   attendance, payroll, and every new module. No per-module fiscal year variants (§3.3).
7. **Every migrated/generated record carries both calendars** (BS and AD) fully populated and
   cross-checked, not just one (§3.3, §3.6).
8. **New company-wide document numbering scheme** — every module that generates a document gets a
   standardized reference code (§3.4).
9. **Full ERP-wide role redesign, named per module** (§4.5, §3.5) — replacing the flat
   admin/viewer/employee + bare operator/incharge/supervisor/marketing/press set with explicit,
   module-scoped role names: `vehicle-admin` (Fleet); `press-admin`/`press-operator`/`press-supervisor`/
   `press-incharge`/`marketing` (Production); `attendance-admin`/`attendance-user` and
   `payroll-admin`/`payroll-user` (the *existing* live Attendance/Payroll system also gets renamed roles,
   with a migration of current users). `admin` remains a system-wide superuser on top of all of these.
10. **Users can hold multiple roles at once** — a new `web_user_roles` table (many-to-many), not a single
    `role` column, since real people plausibly need more than one (e.g. `attendance-user` +
    `payroll-user`, or `vehicle-admin` + `press-incharge`).
11. **Universal read-only list visibility** — every authenticated user, regardless of assigned role(s),
    can view the list/index page of *every* module (Fleet, Production, Attendance, Payroll), independent
    of whether they hold a role there. Detail pages, edit rights, and financial drill-down remain gated by
    the actual module role.
12. **Self-service scope**: `attendance-user` sees only their own attendance/leave data (already exists:
    `/my-attendance`, `/my-leaves`); `payroll-user` sees only their own payroll/payslip data — **this page
    does not exist yet and is new scope** (§4.6).

## 1. Full deno2 table inventory (65 tables), by disposition

### 1a. Excluded — HR/Payroll/Attendance, already exists here (18 tables)
`attendance`, `attendance_monthly_summary`, `attendance_status`, `department`, `designation`,
`education_details`, `employee`, `employee_designation`, `employee_documents`, `employee_family`,
`fiscal_years` *(deno2's copy — not reused, see §3.3)*, `holiday_types`, `holidays`, `leave_balance`,
`level`, `shifts` *(deno2's copy — name collision, see §3.2)*, `zkteco_devices`, `zkteco_pull_log`,
`zkteco_raw_attendance`, `zkteco_settings`, `zkteco_sync_queue`, `zkteco_user_mapping`,
`zkteco_capacity_log`, `ot_rules`.

Confirmed via FK grep across the whole dump: no Fleet or Production table references `employee`,
`department`, `designation`, or `level` directly. Safe to exclude entirely.

### 1b. Identity — merged into `web_users` (data only, no new table)
`users` — **59 real rows (verified)**. See §3.5 for the merge + role-extension procedure.
`audit_log` (deno2's own, 1 table) is **not** re-imported — our existing generic audit-trigger system
covers the new tables the same way it covers existing ones. deno2's historical audit rows are left behind.

### 1c. Fleet / Vehicle Management module (13 tables) — Phase 1, module A
`vehicles` (60 rows), `drivers` (36 rows), `vehicle_driver_assignments` (31 rows), `vehicle_daily_logs`
(53 rows), `vehicle_maintenance_records` (14 rows), `vehicle_audit_logs`, `monthly_vehicle_summary`
(1 row — **verified: generated report, not hand-entered**, see §2), `fuel_coupons` (369 rows),
`fuel_coupon_distributions` (333 rows), `fuel_price_history` (24 rows), `maintenance_parts` (0 rows),
`maintenance_types` (4 rows).

*(`machines` was originally listed here — **corrected, moved to Production, see §1d and §2**.)*

### 1d. Press Production module (19 tables) — Phase 1, module B
Live pipeline (**verified real usage**: 239 job tickets, 2,010 detail lines, 1,294 forma, 1,134 print
runs): `books` (259 rows), `job_ticket`, `job_ticket_details`, `forma`, `forma_printing`, `machines`
(6 rows — **confirmed Production, not Fleet**: `4HI A`–`E`, `Sakurai 4 Color A`, directly FK'd from
`forma_printing.machine_id` and matched in `job_ticket_details.machine`).
Stock/dispatch: `deno` (6,738 rows), `deno_staging` (0 rows), `deno_test` (816 rows), `d2m` (315 rows),
`d2m_items` (5,895 rows), `book_packing` (895 rows).
Prepress/CTP (**verified experimental — near-zero real usage**: `fctp_job_tickets` 1 row, `fctp_formas`
3 rows, `fctp_uploads` 11 rows, `ctp_export_jobs` 0 rows): `page_setups`, `imposition_templates`,
`fctp_books`, `fctp_formas`, `fctp_imposition_templates`, `fctp_job_tickets`, `fctp_uploads`,
`ctp_export_jobs`.

Data for all 19 tables is migrated. **The Phase 1 pilot page is built against `job_ticket`/`forma`/
`forma_printing`** (the live pipeline), not `fctp_*` — the `fctp_*` data is imported and preserved but
flagged as inactive/experimental until you decide whether it becomes the production path later.

### 1e. Deferred to a later phase (8 tables) — data migrated now, no UI yet
`recon_brt` (0), `recon_comparative`, `recon_marketing` (297), `recon_modules`,
`recon_opening_stock_2080`, `recon_pkr` (0), `recon_software`, `recon_stockkeeper` (420).
Reconciliation depends on production data existing to reconcile against, so it naturally follows once
Production is live — data comes in now (cheap), routes/UI wait for a later phase.

### 1f. New, no collision, imported as-is
`system_settings` (5 rows — generic key/value settings, different purpose from `company_settings`, no
merge needed).

## 2. Column-level shape of the two Phase-1 modules

**Fleet:**
- `vehicles(vehicle_id, vehicle_no, vehicle_type, fuel_type, fuel_per_liter_standard, status, remarks, fiscal_year, created_by, updated_by, created_at, updated_at, deleted_at)`
- `drivers(driver_id, driver_name, mobile_no, license_no, status, remarks, fiscal_year, created_by, updated_by, created_at, updated_at, deleted_at)`
- `vehicle_driver_assignments`, `vehicle_daily_logs` (odometer/trip logs, BS+AD date pairs), `vehicle_maintenance_records` (cost/parts/downtime), `vehicle_audit_logs` (historical, read-only import), `monthly_vehicle_summary` (**verified generated**: has a `generated_at` column, populated by the dump's `calculate_monthly_vehicle_summary()` function, no editing trigger — Phase 1 needs no form for this, a later phase adds a "regenerate" action), `fuel_coupons` + `fuel_coupon_distributions` + `fuel_price_history` (allocation → disbursement → price-history chain), `maintenance_parts`, `maintenance_types`.

**Production:**
- `books` — the catalog root.
- `job_ticket` → `job_ticket_details` → `forma` → `forma_printing` (uses `machines`, `press_shifts` — see §3.2) — the live print-run workflow.
- `deno` (+ `deno_staging`, `deno_test` — staging/QA variants of the same shape) — denomination/output stock, linked to `job_ticket`, `book_packing`, `d2m`.
- `d2m` + `d2m_items` — dispatch/handover batching with its own check/verify/send approval chain.
- `book_packing` — final packing stage referencing `job_ticket`.
- Prepress/CTP (`page_setups`, `imposition_templates`, `fctp_*`, `ctp_export_jobs`) — imported, not built into UI in Phase 1 (§1d).

Every `created_by`/`updated_by`/`operator_id`/`supervisor_id`/`incharge_id`/`checked_by`/`verified_by`/
`send_by`/`uploaded_by`/`performed_by` column across both modules is remapped `users(id)` → `web_users(id)`.

## 3. Database plan

### 3.1 Password hash compatibility — verified
deno2's `password_hash` values are `$2y$10$...` (**verified**: 60 chars, PHP-style bcrypt, cost factor 10).
Our `web/auth.py` uses Python's `bcrypt.checkpw()`/`bcrypt.hashpw()` directly, which handles `$2y$` the
same as `$2a$`/`$2b$` — very likely compatible without a forced reset. **Still verify with one real login
test against a migrated account before trusting it for all 59 accounts** — if it fails, fall back to
`must_change_pwd = TRUE` for that account only, not a blanket reset.

### 3.2 Naming collisions
| deno2 table | Collision with | Resolution |
|---|---|---|
| `shifts` (id, name, remarks, status — 7 rows, production shift labels like "6am to 6pm", "7am to 7pm") | our `shifts` (attendance shift with start/end minute windows — different shape/purpose) | Renamed to **`press_shifts`** on import. `forma_printing.shift_id` FK points at `press_shifts`. |
| `fiscal_years` | our `fiscal_years` | **Not reused as-is — replaced by a corrected, unified table.** See §3.3. |
| `audit_log` | our generic audit system | Not imported as a table (§1b). |
| everything else in §1c/§1d/§1f | none | Imported as-is, table names unchanged. |

### 3.3 Fiscal years — single unified standard, re-derived (not trusted from deno2)
**Verified real discrepancy**: deno2's own `fiscal_years` table defines fiscal_code `2082` (label
"2082-83") as **2025-04-14 → 2026-04-13** (mid-April boundary). Our system's `fiscal_years` defines
"2082/83" as **2025-07-17 → 2026-07-16** (Shrawan 1 → Ashad-end — the correct Nepal government fiscal
year, which is what Payroll already runs on). All 239 `job_ticket` rows (and everything under them: 895
`book_packing`, 315 `d2m`) are tagged against deno2's incorrect April-boundary version.

**Resolution — single standard for the whole system**, per your instruction:
- Canonical format: `start_bs = YYYY-04-01`, `end_bs = (YYYY+1)-03-32` (e.g. FY2082-83 =
  `2082-04-01` → `2083-03-32` BS = `2025-07-17` → `2026-07-16` AD) — this is exactly our existing
  `fiscal_years` table's convention, extended backward to cover deno2's historical data range.
- Our `fiscal_years` table currently only has 2 rows (2082/83, 2083/84). deno2's data spans further back
  (`deno.fiscal_year` free-text values found: `2080-81`, `2081-82`, `2082-83` — **verified**). Migration
  **backfills FY2080/81 and FY2081/82** into our `fiscal_years` table using the same Shrawan-anchored
  BS↔AD conversion already used for the existing rows (via `nepali_utils`), so the whole historical range
  has one consistent source of truth.
- **Every migrated row's fiscal year is re-derived from its own actual transaction date** (`date_nep`/
  `date_eng`/`created_at`, whichever the table has), looked up against the corrected `fiscal_years` date
  ranges — deno2's stored `fiscal_year_id`/free-text `fiscal_year` value is used only as a sanity check
  during migration (flagged and reported if it disagrees with the re-derived value), never trusted
  blindly. This is what correctly handles the ~3-month skew: a record dated e.g. May 2025 that deno2
  filed under its "2082/83" will correctly land in **our** FY2081/82 instead, since Shrawan 2082
  (mid-July 2025) hadn't started yet.
- All Fleet/Production tables (`job_ticket`, `d2m`, `book_packing`, `forma_printing` via `fiscal_year_id`;
  `vehicles`, `drivers`, `fuel_coupons`, `deno`, etc. via free-text `fiscal_year` varchar) are converted to
  use a proper `fiscal_year_id INTEGER REFERENCES fiscal_years(id)` FK in the migrated schema — the
  free-text varchar pattern is not carried forward.

### 3.4 Document numbering scheme (new cross-cutting feature)
Format: **`<company_code>/<serial>/<fiscal_year_bs>/<module_code>`** — e.g. `JEMC/1/2082-83/JT` for the
first Job Ticket of FY2082-83.
- `company_code` — new field, added to `company_settings` (currently has no short-code field, only full
  name/address/PAN). You set the actual value once after this ships; not guessed here.
- `serial` — **resets per fiscal year, per module** (matches deno2's own existing `get_next_d2m_serial()`
  pattern, extended to every module rather than just D2M).
- Implementation: one small shared table, `doc_number_counters(fiscal_year_id, module_code, last_serial,
  UNIQUE(fiscal_year_id, module_code))`, and one shared helper `next_doc_number(conn, fiscal_year_id,
  module_code)` (atomic `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`, safe under concurrent writes) —
  used by every module's create-record route, not reimplemented per module.
- Proposed module codes (confirm/adjust when routes are built): `JT` Job Ticket, `FP` Forma Printing,
  `DN` Deno, `D2M` D2M, `BP` Book Packing, `FC` Fuel Coupon, `MR` Maintenance Record, `VL` Vehicle Daily
  Log.
- Historical migrated records from deno2 **keep their original deno2 identifiers** (e.g. `job_ticket_code`,
  `d2m_no`) as-is for traceability — the new numbering scheme applies to records created going forward in
  the integrated system, not retroactively renumbering 6,700+ existing `deno` rows.

### 3.5 Identity + role migration procedure (`users` → `web_users`)
1. Extract deno2's 59 `users` rows (`pg_restore --data-only --table=users`).
2. Username collision check (case-insensitive, matching `web_users`' existing unique index):
   - No collision → insert into `web_users` with a fresh `id`, carry over the bcrypt hash (pending the
     §3.1 verification), set `must_change_pwd` based on that test's outcome.
   - Collision → don't duplicate; use the existing `web_users.id`, flag for manual review (two people may
     have picked the same username independently in the two systems).
3. Build a staging id-map table (`_migration_user_id_map`, dropped after migration) — every Fleet/
   Production data-migration step rewrites `created_by`/`updated_by`/etc. through this map.
4. **Role mapping into the new named catalog** (§4.5) — deno2's real distribution (**verified**):
   `operator` (21), `admin` (20), `marketing` (6), `incharge` (6), `press` (3), `supervisor` (2),
   `viewer` (1). Migrated into the new `web_user_roles` table as:
   - `admin` → legacy `web_users.role = 'admin'` (superuser, unchanged)
   - `operator` → `press-operator`
   - `incharge` → `press-incharge`
   - `supervisor` → `press-supervisor`
   - `press` → *(no direct row — see §4.5, deno2's `press` role was only used as `sender_by` on Deno,
     which is exactly what `press-operator` already covers; not a separate role in the new catalog)*
   - `marketing` → `marketing`
   - `viewer` → legacy `web_users.role = 'viewer'` kept, no module row needed (universal baseline covers it)
   - deno2's unused enum values (`editor`, `presss` — 0 real users on either) are not carried forward.
   - New Fleet accounts get `vehicle-admin` assigned manually post-migration (deno2 had no Fleet role
     differentiation to map from — see §4.5).
   Permission checks for every role in the catalog are **built in Phase 1**, not deferred (§4.5) —
   the earlier draft of this plan deferred RBAC to Phase 2; that's superseded by your direction to build
   it now, evidence-based, module by module.

### 3.6 Migration order (respects FK dependency graph)
1. `fiscal_years` backfill + correction (§3.3) — first, everything else FKs into it.
2. `web_users` identity + role merge (§3.5) — second, everything else FKs into it.
3. **Fleet**: `vehicles` → `drivers` → `maintenance_types` → `vehicle_driver_assignments` →
   `vehicle_daily_logs` → `vehicle_maintenance_records` → `maintenance_parts` → `fuel_price_history` →
   `fuel_coupons` → `fuel_coupon_distributions` → `monthly_vehicle_summary` → `vehicle_audit_logs`.
4. **Production**: `books` → `page_setups` / `imposition_templates` → `machines` → `press_shifts` →
   `job_ticket` → `job_ticket_details` → `forma` → `forma_printing` → `book_packing` → `d2m` →
   `deno` / `deno_staging` / `deno_test` → `d2m_items` → `fctp_books` → `fctp_job_tickets` →
   `fctp_formas` → `fctp_imposition_templates` → `fctp_uploads` → `ctp_export_jobs`.
5. **Reconciliation data** (8 tables, §1e) — loaded last, no UI dependency.
6. **Both-calendar QA pass** (per your instruction): after load, every migrated row is checked that its
   BS and AD date columns are both present and mutually consistent (BS→AD conversion via `nepali_utils`
   matches the stored AD value); mismatches are listed in a migration report rather than silently
   corrected, since a mismatch could mean the *value*, not just one representation, is wrong.

### 3.7 Mechanics
- `pg_restore --data-only --table=<name>` per table (not a blind full restore) — the id-remap (§3.5) and
  fiscal-year re-derivation (§3.3) steps must happen between extraction and load.
- New `CREATE TABLE` DDL follows `db.py`'s existing additive-migration convention
  (`CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN IF NOT EXISTS`) — no new migration framework.
- Views referencing `users` directly (`v_ctp_job_summary`, `v_fuel_coupon_full_details`,
  `v_fuel_price_current`, etc.) are rewritten to reference `web_users`, not imported verbatim.
- Functions/triggers are audited individually; Fleet/Production-relevant ones (`auto_set_fuel_price`,
  `get_current_fuel_price`, `calculate_monthly_vehicle_summary`, `get_maintenance_alerts`,
  `get_next_maintenance_due`, `get_deno_details_for_d2m_item`, `import_deno_from_staging`,
  `log_deno_changes`, `update_deno_fields`) are checked for any hidden `employee`/`users` reference before
  porting; `get_next_d2m_serial` is superseded by the new shared `next_doc_number()` (§3.4).
- Full dry run against a **restored copy of the production DB**, verified clean (row counts, FK integrity,
  both-calendar QA per §3.6), before touching the real database.

## 4. Application layer plan (folder / HTML / CSS — no mismatch with the existing system)

The existing app is a single FastAPI app (`web/app.py`, ~5,800 lines) with all routes inline, a flat
`web/templates/*.html` folder, `db.py` as one large raw-SQL module, and a shared CSS token system
(`web/static/styles/variables.css` + `components.css`/`forms.css`/`tables.css`/etc.).

### 4.1 Backend structure
```
web/
  app.py                  # unchanged existing routes stay here
  routers/                # NEW — one file per new module, FastAPI APIRouter
    __init__.py
    fleet.py               # /fleet/... routes
    production.py           # /production/... routes
  db_fleet.py              # NEW — raw-SQL helpers for the 12 fleet tables
  db_production.py         # NEW — raw-SQL helpers for the 19 production tables
  doc_numbering.py         # NEW — shared next_doc_number() helper (§3.4), used by both routers
```
- `app.py` gains: `from web.routers import fleet, production` +
  `app.include_router(fleet.router, prefix="/fleet")` / `app.include_router(production.router, prefix="/production")`.
- `db_fleet.py`/`db_production.py` reuse `get_connection()` from `db.py`, same function-per-query style
  (`get_all_x`, `create_x`, `update_x`) — a pure organizational split, no ORM, no new patterns.
- Every write route uses the existing `_current_user_id(request)` helper for `created_by`/`updated_by`.
- Existing `render()`, `redirect_with_flash()`, `auth.py` guards, `flash.py` reused as-is.

### 4.2 Templates
```
web/templates/
  fleet/        vehicles.html, vehicle_form.html, drivers.html, fuel_coupons.html, maintenance.html, ...
  production/   job_tickets.html, job_ticket_detail.html, forma.html, deno.html, d2m.html, book_packing.html, ...
```
Jinja2 resolves subfolder template paths natively (`render(templates, request, "fleet/vehicles.html", {...})`)
— no config change. Keeps the flat `web/templates/` root from growing past 60+ files as two genuinely
separate modules arrive. Every new template `{% extends 'base.html' %}`, reuses `components/ui.html`
macros (`badge`, `btn`) rather than reinventing them.

### 4.3 CSS
```
web/static/styles/
  fleet.css         # NEW — module-specific rules only
  production.css    # NEW — module-specific rules only
```
Both built strictly on `variables.css`'s existing design tokens — no new palette, no new spacing scale.
`main.css`/`layout.css`/`components.css`/`tables.css`/`forms.css` reused unmodified; new pages use the
existing `.card`/`.table`/`.form-input`/`.btn` classes rather than parallel CSS for the same widgets.

### 4.4 Navigation
Two new `sidebar-section` blocks in `base.html`, following the exact existing pattern (the accordion
submenu already used for "Reports" is reused for Production's sub-areas):
```html
<div class="sidebar-section">
  <div class="sidebar-section-label">Fleet</div>
  <a href="/fleet/vehicles">Vehicles</a>
  <a href="/fleet/drivers">Drivers</a>
  <a href="/fleet/fuel-coupons">Fuel Coupons</a>
  <a href="/fleet/maintenance">Maintenance</a>
</div>
<div class="sidebar-section">
  <div class="sidebar-section-label">Production</div>
  <button class="sidebar-link sidebar-accordion-toggle">Press Production</button>
  <div class="sidebar-submenu">
    <a href="/production/job-tickets">Job Tickets</a>
    <a href="/production/forma">Forma / Printing</a>
    <a href="/production/deno">Deno</a>
    <a href="/production/d2m">D2M</a>
    <a href="/production/book-packing">Book Packing</a>
  </div>
</div>
```

### 4.5 Roles / access — full ERP-wide RBAC redesign

Two things changed from the first draft of this matrix, per your direction: (1) roles are now named
per-module instead of generic labels, extended across the *whole* ERP including the already-live
Attendance/Payroll system, not just the two new modules; (2) a user can hold **multiple** roles.

#### Data model
- `web_users.role` (existing single VARCHAR column) is **kept** for backward compatibility — `admin`
  remains a system-wide superuser that bypasses every module check below, exactly as it already behaves
  in today's live system. No existing `admin` account or session code changes.
- **New table** `web_user_roles (id, web_user_id, role, granted_by, granted_at)` — many-to-many. A user
  can hold zero or more of the module roles below, on top of (or instead of) the legacy column. Checked
  by one shared helper, `user_has_role(request, role)` / `require_module_permission(request, module,
  action)`, not scattered inline checks.
- **Universal baseline** (applies to every authenticated user, no role row required): read-only access to
  the **list/index page** of every module — Fleet, Production (all 5 sub-areas), Attendance, Payroll.
  Detail pages, edit rights, approvals, and financial figures beyond the list-level summary remain gated
  by the role table below. This is a deliberate transparency choice (per your instruction), not a
  side-effect — worth remembering when reviewing what a given role can actually *change* vs. just *see*.

#### Full role catalog
| Role | Module | Scope |
|---|---|---|
| `admin` | *(all)* | System-wide superuser. Full C/R/U/D + approvals everywhere. Existing role, unchanged. |
| `vehicle-admin` | Fleet | Full C/R/U/D across vehicles, drivers, fuel coupons, maintenance, daily logs. |
| `press-admin` | Production (all 5) | Full C/R/U/D + delete across Job Ticket, Forma/Printing, Book Packing, Deno, D2M. |
| `press-operator` | Production | Create/execute Forma/Printing runs; update Book Packing progress. No delete. |
| `press-supervisor` | Production | Approve/sign-off Book Packing (required step) and Forma/Printing (advisory step). Read elsewhere. |
| `press-incharge` | Production | Create/oversee Book Packing; approve Forma/Printing. Read elsewhere. |
| `marketing` | Deno only | Receive + verify Deno stock handovers from `press-*`. Kept separate from the press-* roles (real, distinct department in the data). |
| `attendance-admin` | Attendance | Full C/R/U/D — devices, employees, attendance logs, leave management, holidays, reports. Replaces today's `admin` for this module's day-to-day operation. |
| `attendance-user` | Attendance | Self-service — own attendance + leave data only (`/my-attendance`, `/my-leaves`, already exist). Replaces today's `employee` role for this module. |
| `payroll-admin` | Payroll | Full C/R/U/D — salary structures, payroll runs, tax slabs, settings. |
| `payroll-user` | Payroll | Self-service — own payslip/payroll data only. **New page required, doesn't exist today — see §4.6.** |

#### Evidence behind the Production role scopes (verified against real deno2 data, not designed blind)
- **`press-operator`**: real usage — creates/runs Forma/Printing (604/896 records), updates Book Packing
  packing progress (836 records).
- **`press-incharge`**: real usage — creates Book Packing jobs (895/895, 100%), approves Forma/Printing
  (1,133 records, the dominant approver there).
- **`press-supervisor`**: real usage — signs off Book Packing (895/895, 100%, the most consistently used
  approval step in the whole dump). Forma/Printing's supervisor step was only actually used 56/1,134
  times historically (mostly defaulted to admin) — kept as an *advisory* step there, not required, unless
  you want to tighten it later (§6).
- **`marketing`**: real usage — receives (5,049) and verifies (2,074) Deno handovers from press, a
  genuine cross-department split confirmed in the data (Press sends 2,075 times as `sender_by`).
- **Job Ticket**: 100% admin historically (239/239 created, 75/75 updated) — `press-admin` covers it;
  `press-operator`/`incharge`/`supervisor` get read-only there (no historical evidence of them creating
  tickets directly).
- **D2M**: 100% admin historically; its `checked_by`/`verified_by`/`send_by` columns are dormant (always
  NULL) — `press-admin` covers it fully, everyone else gets read-only pending the §6 decision on whether
  to activate that dormant chain (mapping naturally to `press-incharge`=check, `marketing`=verify,
  `press-operator`=send, mirroring Deno's live pattern).
- **Delete**: restricted to `admin`/`press-admin`/`vehicle-admin`/`attendance-admin`/`payroll-admin`
  only, everywhere — no non-admin role in the real historical data ever performed a delete-equivalent
  action, and several tables track `deleted_by` as a distinctly more sensitive field than `updated_by`.

#### Migration of existing live Attendance/Payroll users
Per your decision, existing roles are **replaced**, not left alongside the new ones:
| Existing `web_users.role` | Becomes |
|---|---|
| `admin` | Stays `admin` (superuser, unchanged) |
| `employee` | Legacy column value kept as-is for login compatibility; gains `attendance-user` **and** `payroll-user` rows in `web_user_roles` (preserves today's self-service scope, now independently revocable — e.g. a contractor could keep `attendance-user` without `payroll-user`) |
| `viewer` | Legacy column value kept; no module role row needed — the universal read-only list baseline already covers what `viewer` used to mean. Kept only so nothing currently depending on the literal string `"viewer"` breaks. |

This is an additive migration at the DB level (new table, existing column untouched) even though the
*effective* role model for users is a full replacement — chosen specifically so no existing session/auth
code path breaks on day one, while every user still ends up under the new named-role scheme.

### 4.6 New scope: self-service payroll page (`/my-payroll`)
`payroll-user` needing to see "their own payroll data" surfaced a real gap: **no self-service payroll
view exists today** — only `/my-attendance` and `/my-leaves` do. This plan adds `/my-payroll` (mirrors
the existing self-service pattern: resolve the logged-in session's `global_user_id`, list their own
`payroll_items`/generated payslips read-only, no cross-employee visibility) as part of the role redesign
work — without it, `payroll-user` would be a role with nothing to actually view.

## 5. What Phase 1 actually delivers

**In scope:**
- Fiscal-year backfill + correction, unified across the whole system (§3.3).
- `web_users` identity + role merge, id-remap migration, verified against a DB copy first (§3.5).
- Document numbering scheme: `doc_number_counters` table + `next_doc_number()` helper + `company_code`
  field added to `company_settings` (§3.4).
- All 12 Fleet tables + all 19 Production tables created in `db.py`-style DDL, real historical data
  imported with FKs, fiscal years, and user identities all correctly remapped.
- The 8 reconciliation tables' data imported (no routes/UI yet).
- `web/routers/fleet.py` + `production.py` wired into `app.py`; `db_fleet.py` + `db_production.py`;
  `doc_numbering.py`.
- Template/CSS scaffolding (§4.2–4.3), sidebar nav (§4.4).
- **The full ERP-wide RBAC redesign (§4.5)**: new `web_user_roles` table, the complete named-role catalog
  (`vehicle-admin`, `press-admin`/`operator`/`supervisor`/`incharge`, `marketing`, `attendance-admin`/
  `user`, `payroll-admin`/`user`), migration of existing live Attendance/Payroll users into it, the
  universal read-only list-view baseline, and the shared `require_module_permission()` helper — covering
  the *existing* live system as well as the two new modules, not deferred to a later phase.
- **`/my-payroll` self-service page (§4.6)** — new, doesn't exist today, needed for `payroll-user` to have
  anything to view.
- **One working end-to-end page per module**: Vehicles list/detail (Fleet) and Job Tickets list/detail
  (Production) — real data, real create/edit forms using the new doc-numbering scheme and the real RBAC
  gating, matching the existing app's look exactly. This is the pattern later phases replicate for the
  remaining tables (Forma/Printing, Book Packing, and Deno's press↔marketing handover get the same
  permission model applied when those pages are built next).

**Explicitly out of scope for Phase 1** (later phases):
- Full CRUD *pages* for every Fleet/Production table beyond the two pilot pages — the RBAC matrix (§4.5)
  is defined for all of them now, but the UI to exercise most of it (Forma/Printing, Book Packing, Deno's
  send/receive/verify actions, D2M) is built module-by-module in later phases, against the permission
  rules already locked in here.
- CTP/imposition PDF generation pipeline; deciding whether `fctp_*` supersedes the current pipeline.
- Reconciliation module UI.
- Cross-module ERP dashboard / unified home page.

## 6. Remaining open items

1. **Exact module code list** for the doc-numbering scheme (§3.4) — the proposed `JT`/`FP`/`DN`/`D2M`/
   `BP`/`FC`/`MR`/`VL` set is a reasonable default but should be confirmed/adjusted once each module's
   routes are actually being built, in case the organization has existing conventions for some of these.
2. **`company_code` value** — needs to be set once `company_settings` gets the new field; not guessed here.
3. **Password-hash live test** (§3.1) — do the actual "log in with one migrated deno2 account" test before
   trusting bcrypt compatibility for all 59 accounts.
4. **`fctp_*` fate** — data is migrated either way, but whether it eventually replaces
   `job_ticket`/`forma`/`imposition_templates` as the live pipeline, or is retired, is a product decision
   for a later phase once Production is live and you've seen both in use.
5. **D2M's dormant check/verified/send workflow (§4.5)** — activate it now (extending the real Deno
   press↔marketing pattern to D2M) or keep D2M admin-only, matching how it was actually used historically.
6. **Forma/Printing's `supervisor` approval step (§4.5)** — was only really used 56/1,134 times
   historically (mostly defaulted to admin). Make it a required gate going forward (like Book Packing's,
   which was consistently used) or keep it optional/advisory, matching its actual historical looseness.
7. **Fleet role scope (§4.5)** — `vehicle-admin` is the only Fleet-specific role since zero historical
   role differentiation exists to derive anything finer from. If you want e.g. drivers or an incharge to
   see their own vehicle/logs going forward, that's a new access pattern to design, not a migrated one.
8. **Who gets assigned `vehicle-admin`/`press-*`/`attendance-*`/`payroll-*` initially** — the role
   *catalog* and permission logic are built in Phase 1, but actually assigning them to specific real
   people (beyond the historical deno2 mapping in §3.5, which only covers Production roles) is an
   administrative task for you to do via the web UI once it ships, not something this plan can decide.

## 7. UI/UX cross-cutting standards — documented now, built later

Per your instruction, this section is **design-only** — nothing here has been implemented. It's recorded
now so these four decisions are locked in and consistent before any module UI (Fleet, Production, and
eventually a Payroll/Attendance retrofit) gets built, rather than each page inventing its own pattern.

### 7.1 Role-based dashboard with widgets

A single dashboard (`/`) that shows different widgets depending on which role(s) the logged-in user
holds (§4.5's `web_user_roles`), each with a card-style figure and, where the underlying data supports a
trend, a small chart — not a static number wall.

Proposed widget catalog per role (adjust when actually built — this is a starting design, not a
locked spec):

| Role | Widgets |
|---|---|
| `admin` | Everything below, in one combined view. |
| `vehicle-admin` | Fleet size (active/inactive count), fuel consumption trend (line, last 6 months), vehicles due for maintenance (list card), fuel coupon balance remaining (gauge/bar per vehicle). |
| `press-admin` | Job tickets open vs. completed (funnel: ticket → forma → printed → packed → dispatched), Forma/Printing throughput trend (bar, runs per week), Book Packing backlog (card + list). |
| `press-operator` | My active print runs / packing jobs today (list card), my throughput this week (simple trend). |
| `press-supervisor` / `press-incharge` | Pending approvals queue (list card — this is the actionable one, most important widget for these two roles), approval turnaround time trend. |
| `marketing` | Deno pending receipt (list card), Deno pending verification (list card), receive/verify volume trend. |
| `attendance-admin` | Present/absent/leave breakdown today (donut), attendance trend (line, last 30 days), pending leave applications (list card). |
| `attendance-user` | My attendance this month (calendar-style card), my leave balance (bar per leave type). |
| `payroll-admin` | Latest payroll run status/total (card), payroll cost trend (line, last 12 months), unconfirmed tax slabs / employees missing salary setup (warning list card). |
| `payroll-user` | My latest payslip summary (card), my net pay trend (line, last 12 months) — feeds off the new `/my-payroll` page (§4.6). |

Universal baseline: every role also sees a read-only "all modules" summary strip (per §4.5's universal
list-view visibility) — counts only, no charts, linking out to each module's list page.

### 7.2 Card view / table view toggle, per module list page

Every module's list page (Vehicles, Job Tickets, Forma/Printing runs, Deno entries, Book Packing jobs,
etc.) gets a view toggle — the same dataset rendered either as a data table (current convention, e.g.
`employees.html`) or as a card grid (better for touch/tablet use on a shop floor, which fits `press-operator`
usage in particular). Persisted per-user (a `localStorage` preference, not a DB column — cheap, no schema
needed) so a user's choice sticks across visits without server-side state.

### 7.3 Server-side pagination — now a system-wide standard, not just audit log

The audit log page (§ this session's live change) is the reference implementation: `LIMIT`/`OFFSET` in
the query, a `page` query param, filtered-count-aware Prev/Next controls. Every new module list page
(Fleet, Production) and any *existing* page that currently loads an unbounded result set follows the same
pattern going forward — a later pass should audit existing pages (e.g. `/employees`, `/attendance`) for
any that currently pull everything client-side and retrofit them to match.

### 7.4 Soft-delete only — no hard delete, anywhere

Every table gains (or already has, per §3.6's convention audit) `deleted_at TIMESTAMPTZ` +
`deleted_by INTEGER` columns; a "delete" action is always an `UPDATE ... SET deleted_at = NOW(),
deleted_by = %s`, never a `DELETE FROM`. Every list/report query gets a `WHERE deleted_at IS NULL` filter
(matching the pattern already fixed for `get_all_global_users_with_dept()` earlier this session).

**This is a bigger change than it looks for the *existing* live system** — a real audit is needed before
touching anything, not assumed here:
- Several existing routes currently do hard deletes (e.g. `delete_employee_record`/
  `bulk_delete_employee_records` for device-level `employees` rows, which also cascade-delete attendance
  logs) — converting these to soft-delete changes their behavior (rows and their attendance history stay
  in the DB, cascading FK deletes no longer happen) and needs your sign-off table-by-table, not a blanket
  find-and-replace.
- Every report/list query across the whole app needs the `deleted_at IS NULL` filter added, or a
  soft-deleted row silently reappears everywhere (exactly the class of bug already fixed once this session
  for `get_all_global_users_with_dept()` — that fix is the pattern, this is applying it universally).
- New Fleet/Production tables get this from day one (deno2's own tables already mostly have `deleted_at`/
  `deleted_by` columns — verified in §2's column lists — so this mostly means *using* those columns via
  application-level soft-delete instead of ever issuing `DELETE FROM`, not adding new columns).

Recommend a dedicated follow-up pass for the existing-system half of this (find every real `DELETE FROM`
in `db.py`/`web/app.py`, convert one at a time, re-check every list/report query for the missing filter)
rather than doing it inline with the Fleet/Production build — flagged here so it isn't forgotten, not
scheduled into a specific phase yet.
