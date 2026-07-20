# Payroll ↔ Attendance Integration Plan

Status: **All 11 phases implemented, plus a post-Phase-9 addition (Tax Projection report).** See below for what was built in each, and the record of decisions made along the way.

## Post-Phase-9 addition: Tax Projection (yearly tax prediction + monthly deduction forecast)

Requested directly after the 11 phases shipped: a report that predicts an employee's annual tax liability and monthly deductions **without requiring an actual payroll run to exist first** — for HR planning, distinct from Section 2.8's Annual Payroll Summary (which only aggregates *actual* generated runs).

- `GET /payroll/tax-projection` (+ `/excel`) — two views in one route: no `global_id` → a register (one row per employee with a BASIC head configured, showing projected annual gross/taxable/tax); `global_id` given → one employee's full 12-month breakdown with a per-month head/deduction drill-down.
- `db.get_tax_projection_for_employee()` simulates all 12 months of the fiscal year using the employee's **current** salary head/deduction configuration, reusing the exact same `resolve_employee_heads_for_month()` → `resolve_employee_deductions_for_month()` → `payroll.compute_payslip()` cumulative-TDS pipeline proven in Phase 6 — this is not a separate estimate engine, it's the same code path payroll generation itself uses, just run forward from configuration instead of backward from persisted `payroll_items`.
- **Integrated with attendance, per your instruction**: a month that has already happened (or is in progress) pulls real attendance via `get_month_attendance_summary()` (Phase 7's shared engine); a future month assumes full attendance (there's no way to know future punches yet) and is labeled `projected` vs `actual` in the UI so the distinction is never hidden.
- One-time/annual heads (e.g. Dashain) correctly appear only in their configured `pay_bs_month` — verified directly: a synthetic employee's Dashain head showed up in exactly one of the 12 projected months, with the expected tax spike that month from the cumulative TDS true-up.
- Verified end-to-end against live data: 12 months generated, actual/projected split correctly at the real current-date boundary (1 actual + 11 projected, matching today falling inside the first month of the newly-active fiscal year), monthly gross figures summed exactly to the reported annual total, the register's per-employee row matched the detail view's own computation, both HTML views and the Excel export rendered without error. Route count confirmed +2 (146 → 148), server restarted clean. All test configuration removed afterward (0 rows remaining).

**Note on why some payroll reports appeared empty when this was raised**: confirmed directly against the live database — zero employees currently have any salary heads, deduction enrollments, or payroll runs configured. This isn't a bug in the reports; there is genuinely no payroll data yet. The Tax Projection register above will also show nothing until at least one employee is configured via `/payroll/employee/{id}/setup`.

---

**Phase 1 — done:**
- `fiscal_years` table (schema in `db.py`, Phase 18 block) — seeded on first run with the currently-active fiscal year computed from today's BS date via `nepali_utils`, not hardcoded. Verified: seeded row for FY2083/84 matches `2083-04-01`→`2084-03-32` BS / `2026-07-17`→`2027-07-16` AD exactly.
- `payroll_tax_slab_sets` + `payroll_tax_slab_bands` — replace `payroll.py`'s hardcoded `_SLABS` dict. Seeded with the corrected single/married bands (top 39% band added, fixing the bug in Section 0 gap #3). `payroll.annual_tax()`/`slab_breakdown()`/`monthly_tds()`/`compute_payslip()` now take a `bands`/`tax_bands` argument instead of a `marital` string with an internal lookup.
- `fiscal_period_index()` accepts a `fiscal_start_month` argument instead of hardcoding Shrawan.
- `company_settings.default_shift_start_min`/`default_shift_end_min` — replace the `SI_MIN, SO_MIN = 600, 1020` literals hardcoded 3× in `web/app.py`, via `db.get_default_shift_window()`.
- `payroll_runs.fiscal_year_id` FK added; `/payroll/runs/generate`, `/payroll/runs/{id}/item/{gu_id}`, `/payroll/runs/{id}/payslip/{gu_id}`, and `/payroll/tax-preview` all resolve the fiscal year and its confirmed tax slabs dynamically, and refuse to generate/edit against a `closed`/`locked` fiscal year or an unconfirmed slab set.
- `test_payroll.py` — acceptance test (9 checks, all passing): reference-sheet reproduction, the top-band fix, configurable fiscal period index, and the `tax_bands`-required guard.
- Verified end-to-end against the live DB: schema migration is idempotent and warning-free, and a full synthetic payroll-generation round trip (salary structure → fiscal year resolution → tax band resolution → `compute_payslip` → persisted `payroll_items` row) was run and cleaned up, leaving the live database exactly as it was before.
- **Bug fixed along the way**: the original Phase 17 audit-trigger loop iterated `_AUDITED_TABLES` unconditionally; adding Phase 18's tables to that list broke it on every server start (the whole audit-trigger migration silently rolled back, for *every* table, not just the new ones) because those tables don't exist yet when Phase 17 runs. Fixed by making the loop skip not-yet-existing tables per-table instead of failing the whole phase.

**Phase 2 — done (DB-level correctness only; display switch deliberately deferred):**
- **Found a blocking data gap while starting this phase**: `global_users.employee_id` (the field Section 3.2 designates as the Master Employee ID) is populated for only **2 of 495 employees** — confirmed live. This is exactly Section 2 Question 10's concern. Switching report/payslip "ID" displays to `employee_id` now would blank the column for 493 people.
- **Decision (confirmed with you)**: hold off on the display switch entirely. Reports/payslips keep showing `global_user_id` as "ID" for now — no template changes in this phase. The display switch is deferred until `employee_id` is actually populated for everyone (still Section 2 Q10, still open).
- **What was safe to do and is done**: `payroll_items` (the one genuinely point-in-time payroll record) now carries a denormalized `employee_id_snapshot` + `employee_name_snapshot`, stamped from `global_users` at the moment each payslip line is generated/regenerated (`db.py` Phase 19 block + updated `insert_payroll_item()`). This is correct and inert regardless of the data-gap question — it'll just read `NULL` for the 493 employees without a master ID yet, which is honest, not broken, and it means the day `employee_id` *is* populated, historical payslips generated before that point still correctly show what was true when they were generated, per Section 3.2's whole point.
- **Audit checklist (Section 3.3) re-verified** for all payroll tables: `payroll_salary_structures`, `payroll_runs`, `payroll_items`, `payroll_holiday_ot_rules`, `fiscal_years`, `payroll_tax_slab_sets`, `payroll_tax_slab_bands` are all in `_AUDITED_TABLES` with a working trigger. `payroll_salary_structures`/`payroll_holiday_ot_rules` are ongoing config, not point-in-time records, so no snapshot columns needed there per Section 3.2's distinction.
- Verified end-to-end against the live DB: schema migration clean, and a synthetic payroll-item insert confirmed `employee_id_snapshot`/`employee_name_snapshot` exactly match `global_users` at insert time — then fully cleaned up.

**Phase 3 — done (audit only, per its own definition — "not as a separate migration"):**
- Re-ran the Section 4 convention checklist against every table Phases 1–2 introduced. Result: fully compliant, nothing to fix. `fiscal_years` is the only table with genuine business date fields (`start_bs`/`start_ad`, `end_bs`/`end_ad`) and they're correctly paired since Phase 1 built it that way from the start. `payroll_tax_slab_sets`, `payroll_tax_slab_bands`, and the `payroll_items` snapshot columns have no date fields to pair. `payroll_runs`' existing `bs_year`/`bs_month` resolve their AD range via the new `fiscal_year_id` FK rather than needing redundant date columns — same "inherit, don't duplicate" reasoning as `payroll_tax_slab_sets` not storing its own effective dates (Section 6.1).
- This checklist gets re-applied automatically as part of each remaining phase's own schema work, as the plan always specified — Phase 4's `payroll_employee_heads.effective_bs` and later `payroll_attendance_snapshot`'s date fields will be checked against it when those phases ship, not retrofitted separately.

**Phase 4 — done (schema + catalog only; not yet wired into payroll generation — that's Phase 8):**
- `payroll_heads` — the company-wide catalog, seeded with the 11 earning heads from your reference sheet (Basic, DA, Upadan, Allowance, Tiffin, Medical, Dress, Dashain, Copy, Barshik, Rahat). OT is deliberately excluded — it's computed dynamically from attendance, not a static head, unchanged from today's engine.
- `payroll_employee_heads` — per-employee amounts, with `percent_of_basic` heads (DA, Upadan) storing **no amount at all** — they're computed live from the employee's own BASIC row every time they're resolved, so a Basic-salary raise automatically flows into DA/Upadan without a separate edit anywhere.
- Added a `frequency_override` column (not in the original Section 2.2 design) to handle **Rahat** specifically — your very first message flagged it as "Company policy — Monthly or yearly," i.e. ambiguous per employee, not a fixed company-wide rule like the others. This lets an individual employee's Rahat be monthly while the catalog default stays annual, without forcing a schema change later.
- `resolve_employee_heads_for_month(conn, global_user_id, bs_month)` — the read path Phase 8 will call: returns only the heads actually due that month (monthly heads always, annual/festival/onetime heads only when their `pay_bs_month` matches), with percent-of-basic heads resolved to actual amounts.
- One-off migration (`_seed_payroll_heads_and_migrate_structures`): any existing `payroll_salary_structures.basic_salary`/`allowances` row is copied into `BASIC`/`ALLOWANCE` head rows, `ON CONFLICT DO NOTHING` so it never overwrites a later admin edit and re-runs safely on every server start (picks up anyone still using the legacy `/payroll/salary-structures` form until Phase 8 cuts the UI over). Verified against a synthetic legacy structure — migrated correctly, then cleaned up.
- Verified end-to-end: catalog seeds to exactly 11 heads with the right codes/types; a synthetic employee's DA/Upadan resolved to **4,708.63** and **2,825.18** on a 47,086.33 Basic — exact matches to your original reference sheet; Dashain correctly appears only in its configured `pay_bs_month` and is absent every other month; a Basic-salary raise was confirmed to propagate into DA/Upadan automatically. All test data cleaned up afterward.

**Phase 5 — done (schema + catalog only; not yet wired into tax computation — that's Phase 6):**
- **You hadn't answered Q1–Q3 yet** — rather than block, this phase seeds directly from numbers you'd already given me (your reference sheet + the Nepal tax-deduction rules you pasted), same "seed from a known-good source, mark for confirmation" approach as Phase 1's tax slabs. Flagged explicitly in `db.py`'s seed comment as not independently verified against the Finance Act/IRD.
- `payroll_deduction_types` + `payroll_employee_deductions` — the exact same generalized pattern as Phase 4's earning heads (catalog + per-employee row, `calc_type` = `fixed` or `percent_of_basic`, never hardcoded per deduction name), extended with `cap_amount`/`cap_percent_of_gross` so a statutory cap is data, not a special case. Confirmed you want this same flat/percent pattern reused everywhere, not just for DA/Upadan — it already was designed that way in both Phase 4 and this phase.
- Seeded: **PF** — 20% of Basic (matches your sheet's 113,007.20 = 565,036 × 20%), capped at the lesser of ⅓ of gross or Rs 500,000/yr (both caps set; resolver applies whichever binds). **CIT** — flat Rs 36,000/yr, matching your sheet. **INSURANCE** — no universal default (premium varies per employee), capped at Rs 40,000/yr (life insurance), matching both your sheet's figure and the statutory cap. The Rs 20,000/yr health-insurance cap is *not* modeled as a separate type yet — add a `HEALTH_INSURANCE` catalog row when needed, no schema change required. CIT's possible combined cap with PF under Nepali law is *not* implemented — flagged as an open gap, not silently assumed.
- **Caught and fixed a unit bug before it ever ran**: statutory caps (500,000/yr, 40,000/yr) are always annual figures regardless of how often the deduction itself is paid, but the first draft of the cap-conversion logic tied the annual→monthly conversion to the deduction's own payment frequency instead. Fixed so caps always convert on their own annual basis.
- `resolve_employee_deductions_for_month(conn, global_user_id, basic_amount, gross_amount)` — the Phase 6 read path: resolves each enrolled deduction to a monthly Rs figure with caps applied, and reports whether a cap actually engaged (for the Section 9 formula display).
- Verified end-to-end against your reference sheet: resolved PF = **9,417.27**/mo and CIT = **3,000.00**/mo — exact matches. Also verified the PF cap correctly engages for a high-salary synthetic case (Rs 300,000/mo Basic → uncapped PF would be Rs 60,000/mo, correctly clamped to Rs 41,666.67/mo = 500,000/12). All test data cleaned up afterward.

**Phase 6 — done. This is the acceptance test for the whole plan, and it passes exactly:**
- `payroll.compute_payslip()` gains a `pretax_deductions` parameter (accepts either `db.resolve_employee_deductions_for_month()`'s resolved detail list, or a plain number for simpler callers) and now actually computes `taxable_this_month = gross - pretax_deductions_total` (floored at zero), instead of the previous `taxable_this_month = gross` that ignored deductions entirely (Section 0 gap #6/#3.2 — Section 6.3's fix, finally wired in). Non-pretax, ad-hoc deductions still flow through the existing `other_deductions` param unchanged (post-tax, from net pay only).
- The resolved `pretax_deductions_total` and per-item detail are returned in the payslip dict, ready for the Section 9 formula display once the templates are built (Phase 8+).
- `test_payroll.py` extended with two new sections: **[5]** proves the mechanic in isolation (taxable income is `gross` with no `pretax_deductions` passed — old behavior preserved for anyone not yet using it — and strictly lower once deductions are supplied). **[6]** simulates a full 12-month fiscal year using PF/CIT/Insurance figures that exactly match your reference sheet, running through the real cumulative-TDS true-up logic (unchanged from before) — **the simulation converges to your reference sheet's annual figures exactly**: cumulative gross **922,590.68**, cumulative taxable income **733,583.48**, cumulative tax **19,358.35** — all three matching your original sheet to the paisa, not just within the tolerance the test allows for.
- Also ran a live end-to-end integration check tying Phase 4's `resolve_employee_heads_for_month()` → Phase 5's `resolve_employee_deductions_for_month()` → Phase 6's `compute_payslip()` together against the real database (not just isolated unit tests) — confirmed the three phases' outputs actually compose correctly, then cleaned up.
- All 18 acceptance-test checks pass; nothing simulated or mocked in the tax math itself.

**Phase 7 — engine unification + snapshot persistence done; report page still to come:**
- **This was the highest-risk change in the whole plan** — it touches the live production code behind `/reports/monthly/view`, `/reports/monthly/print-all`, and `/reports/hajiri`, actively used reports, not just new payroll scaffolding. Handled as a pure relocation, not a rewrite, and verified accordingly.
- `_compute_monthly_report`/`_monthly_totals`/`_fmt_min`/`_time_to_min` extracted verbatim from `web/app.py` into a new `attendance_engine.py` at repo root. `web/app.py`'s three report routes now import from there instead of defining the logic inline — zero behavior change, confirmed by re-running the extracted engine against real production data for the same employee/month shown in an earlier screenshot this session (Yadunath Poudel, Ashar 2083) and getting byte-identical numbers (10:01 in/10:01 work day 1, 09:49 in/08:05 work/01:05 OT day 2, etc.).
- Payroll's old `attendance_daily`-based helpers (`get_month_present_days`, `get_month_ot_split`, `get_month_ot_hours`) are fully retired — not just superseded, deleted — replaced by `get_month_attendance_summary()`, which computes present/absent/leave/OT figures via the *exact same* `attendance_engine` pipeline the reports use. This closes the drift risk flagged back in Section 3/0 gap #10: a payslip's numbers can no longer silently disagree with what `/reports/monthly/view` shows for the same employee/month, because they're now computed by the same code.
- **The paid-leave fix is now live**: `get_month_attendance_summary()` returns `paid_days` (present + paid leave, capped at working days) alongside raw `present_days`, using `leave_types.is_paid` to distinguish paid from unpaid leave dates. `payroll_generate`/`payroll_edit_item` now pass `paid_days` to `compute_payslip()` instead of raw present-days-only — an employee on approved paid leave no longer loses salary for those days.
- **Found and fixed a real gap while building this**: `attendance_engine.compute_monthly_report()` never flags the `ot` field on off-days (Weekend/Holiday/Festival) at all — the OT check is explicitly gated by `not is_off_day`. So "holiday OT" (working on a day off), which Phase 14's holiday-OT-multiplier feature depends on, isn't visible in the `ot` field the way the old `attendance_daily.status_code`-based split assumed. Fixed in `get_month_attendance_summary()`: any `work_min` recorded on an off-day counts entirely as holiday OT (since planned work is 0 that day, all of it is overtime) — computed directly from the day list rather than relying on the `ot` field for those rows.
- New `payroll_attendance_snapshot` table (Section 8.1) — persisted, not recomputed-and-discarded. `payroll_generate` and `payroll_edit_item` both call `save_payroll_attendance_snapshot()` after resolving attendance, so every payroll run has an immutable point-in-time record of "days working and all" for that employee/month, with the master `employee_id` denormalized (Section 3.2). Added to `_AUDITED_TABLES` — regenerating a run after a punch correction produces a real old/new diff in `audit_log`, the change-log capability asked for explicitly.
- Verified end-to-end: schema migration clean; day-count consistency checks (present+absent+weekend+holiday+festival+paid_leave+unpaid_leave always sums to total_days) pass against real data; snapshot persists and reads back exactly; re-saving (simulating a run regeneration) updates the existing row in place rather than duplicating it. All test data cleaned up.
**Attendance-for-Payroll summary report (Section 2.7/7) — also done:**
- New `GET /payroll/attendance-summary` route + `payroll_attendance_summary.html` template. Reuses `get_employees_for_report()` (the same picker source `/reports/monthly` uses) with search/department/section filters. Inherits `/payroll`'s existing admin-only RBAC automatically (prefix match, no new rule needed).
- **Live preview vs. persisted snapshot, exactly as Section 8.1 specified**: if a `payroll_runs` row already exists for the selected BS year/month, each employee's row reads from their persisted `payroll_attendance_snapshot`; if no run exists yet, it's computed live via the same `get_month_attendance_summary()` Phase 8 will use — so admins can review before generating, and the page clearly labels which mode it's in ("live preview (not yet generated)" vs "snapshot (already generated)").
- **"Ready for payroll" status** per employee: `not_configured` (no salary structure yet), `no_attendance` (configured but zero present/paid-leave days — the actual warning case the plan asked for), or `ready`.
- Uniform report UX (Section 7): search/filter toolbar, Print, Download PDF (single-document `html2pdf` pattern matching `/reports/dept-attendance`'s existing convention — a tabular register report, not per-employee payslip cards, so the per-card worker-chain pattern from the earlier attendance print-all work didn't apply here), and Excel export (`/payroll/attendance-summary/excel`, matching `/reports/daily/excel`'s exact openpyxl styling — company header, blue header row, alternating row shading, auto-sized columns). Bulk-editable Excel upload/sample-template doesn't apply — this report is read-only, not bulk-editable data.
- Added to the Payroll sidebar section in `base.html`.
- Verified against live data: filtered the real employee list down to a single search match, confirmed the `not_configured`/`ready` status transitions correctly when a salary structure is added/removed, and ran the Excel export function directly (bypassing HTTP, since I don't have login credentials for this system) — produces a valid `.xlsx` `StreamingResponse` with no errors. Full app import confirmed clean (route count increased by exactly the 2 new routes, 128 → 130). Server restarted and boots with no errors. All test data cleaned up afterward.

**Phase 8 — done. Payroll generation is now fully wired through heads, deductions, attendance, and the fiscal-year tax engine:**
- `/payroll/runs/generate` no longer reads `basic_salary`/`allowances` directly — it resolves each employee's earning heads via `resolve_employee_heads_for_month()` (Phase 4), splits them into monthly heads (prorated by attendance, exactly like the old basic+allowances path) and one-time/annual/festival heads actually due that BS month (paid in full via `other_earnings`, un-prorated — festival/annual benefits aren't typically prorated by daily attendance). `payroll_salary_structures` stays in use, but now only for its policy fields (`daily_hours`, `ot_multiplier`, `marital`, `other_deductions`) — never for earnings amounts, per Section 2.2's original design.
- **Deduction basis decision** (not explicitly specified in the plan, made and documented here): PF/CIT/Insurance are resolved from the employee's *entitled* (un-prorated) Basic/gross, not the attendance-prorated figure — matching how the reference sheet computes retirement contributions on full monthly Basic regardless of days present. If this should instead scale with attendance, it's a one-line change in `payroll_generate`/`payroll_edit_item` (pass the prorated gross instead of `gross_unprorated`).
- New `payroll_item_heads`/`payroll_item_deductions` child tables — the immutable per-run breakdown Section 2.5 asked for. Stamped from the catalog at generation time (code/name/category/amount copied in, not FK'd), so a later catalog edit (rename a head, change a rate) never rewrites what a historical payslip already showed. `insert_payroll_item()` now returns the item's id so `save_payroll_item_breakdown()` can attach to it in the same request.
- `/payroll/runs/{id}/item/{gu_id}` (the manual OT/adjustment override) updated the same way — resolves heads/deductions fresh on every recalculation, so a manual override never falls back to stale flat figures.
- `payroll_payslip.html` now renders the actual formula (Section 9): every earning head by name and amount, every deduction by name and amount with a "capped" badge when a statutory cap engaged, the pre-tax deduction total shown as its own line before tax, and both this-month and YTD taxable income — not just final totals. Falls back to the old flat Basic/Allowance display if no heads are configured for an employee yet (so nothing breaks for anyone not yet migrated onto the head model).
- Verified end-to-end with a full synthetic run mirroring `payroll_generate`'s exact code path (Basic 47,086.33 + DA/Upadan/Allowance/Tiffin/Medical, a Dashain head due only in BS month 6, PF/CIT/Insurance enrolled): confirmed Dashain correctly **excluded** from a month-4 run, confirmed PF/CIT resolved to the exact reference-sheet figures (9,417.27 / 3,000.00), and confirmed the persisted `payroll_item_heads`/`payroll_item_deductions` rows exactly match what was resolved (6 heads, 3 deductions, right codes, right amounts). Full app import and a live server restart both clean, no errors. All test data — including the payroll run, items, breakdown rows, snapshot, and employee configuration — fully cleaned up afterward (verified 0 rows remaining in every touched table).

**Phase 9 — done. New reports: bulk payslip download + Annual Payroll Summary:**
- **Bulk payslip download** (`GET /payroll/runs/{id}/payslips/print-all` + `payroll_payslip_print_all.html`) — reuses the exact per-card `html2pdf` worker-chain pattern (sequential rendering, progress indicator, dark legible text) built for the attendance print-all report earlier this session, adapted to the payslip's own layout (salary-slip card, not a 16-column attendance table). Shows the same head/deduction formula breakdown as the single-payslip view, for every employee in the run, one PDF page each. Linked from `payroll_run.html`.
- **Annual Payroll Summary** (`GET /payroll/annual-summary` + `.../excel` + `payroll_annual_summary.html`) — new `get_annual_payroll_summary()`/`list_employees_with_payroll_in_fy()` in `db.py` aggregate every `payroll_items`/`payroll_item_heads`/`payroll_item_deductions` row across all of a fiscal year's runs for one employee: per-head annual totals, per-deduction annual totals, gross/taxable/tax/net reconciliation, and the annual tax-slab breakdown — matching the layout of your original reference sheet (Section 2.8), with the same "How This Was Calculated" formula panel as the payslip (Section 9). Fiscal-year + employee picker, Print, Download PDF, and Excel export (matching the established openpyxl styling convention).
- Both added to the Payroll sidebar.
- **Verified with a genuine 2-month aggregation test** (not a 1-month stand-in) — generated real `payroll_runs`/`payroll_items` for BS months 4 and 5 of FY2083/84 for the same test employee, confirmed the annual summary correctly summed BASIC across both months (94,172.66 = 47,086.33 × 2) and PF across both months (18,834.54 ≈ 9,417.27 × 2), confirmed both the HTML route and the Excel export render/execute against that real data without error, then fully cleaned up (0 rows remaining in every touched table). Full app import and a live server restart both clean (route count +3 as expected, 130 → 133).

**Answers received on the remaining open questions:**
- **Q1 (PF split)** — not a flat 20% company-wide constant as originally seeded: **PF percentage varies per employee, tied to join date/tenure policy.** The schema already supported this without a migration — `payroll_employee_deductions.percent_override` exists specifically for cases where a catalog default doesn't apply uniformly. Phase 10's per-employee setup page (`/payroll/employee/{gu}/setup`) surfaces the employee's **join date** directly alongside the PF override field so whoever sets it has the information needed to apply the right tenure-based rate. The 20% catalog default stays as a starting point for employees who haven't been individually configured yet — it is **not** the actual rate for everyone, and should not be relied on without setting per-employee overrides.
- **Q2 (CIT/Insurance)** — confirmed: company-wide default + per-employee override is the right model. Already built this way in Phase 5, unchanged.
- **Q10 (`employee_id` gap)** — confirmed: you'll import the missing master IDs. Until then, the UI (payslips, reports, the employee setup page) continues showing `global_user_id` as the primary visible ID, with an explicit on-page warning wherever `employee_id` is blank, rather than pretending it's populated.

**Phase 10 — done. Full management UI — everything is now editable without a code deploy:**
- **`/payroll/settings`** — Fiscal Years (create with just a BS label like `2084/85`, AD/BS dates computed automatically; status transitions upcoming→active→closed→locked), the Salary Heads catalog (add/edit/activate/deactivate — Flat vs. Percent-of-Basic is a literal dropdown, exactly as requested, and the same dropdown pattern is reused identically for deduction types), the Statutory Deduction Types catalog (same pattern, plus the two statutory caps), and the Default Shift Window.
- **`/payroll/tax-slabs`** — per-fiscal-year band editor for Single / Married / Unified (`ALL`), with an explicit "confirmed against enacted Finance Act / IRD guidance" checkbox and source-note field — matches Section 6.1's `is_confirmed` gate exactly; an unconfirmed slab set still blocks generation (enforced since Phase 6).
- **`/payroll/employee/{gu}/setup`** — the per-employee heads/deductions screen, with join date, master ID (and a warning banner when it's missing), and marital status all visible on one page. Percent-of-basic heads/deductions show "computed live" instead of an amount field, matching how they actually resolve.
- Editing a catalog head or deduction type here **never rewrites a historical payslip** — `payroll_item_heads`/`payroll_item_deductions` are stamped copies from generation time, not live references, verified again in this phase's testing (catalog update path exercised directly, confirmed it doesn't touch existing item rows since none existed to touch).
- Verified end-to-end: settings/tax-slabs/employee-setup pages all render against real data; catalog create+toggle+update round-tripped correctly for both heads and deduction types; the shift-window update round-tripped and was restored; a real tax-slab-set replacement was exercised against the live active fiscal year (values were identical to the seed, confirmed a genuine no-op, and the overwritten `source_note` metadata was restored afterward since this is a live company database, not a scratch environment). Full app import clean (route count +13 as expected, 133 → 146). Server restarted, boots with no errors.

**Phase 11 — done. Rollout & verification:**
- **Side-by-side reference-sheet check**: already satisfied by Phase 6's 12-month simulation (exact match to the rupee: gross 922,590.68, taxable 733,583.48, tax 19,358.35) and Phase 9's real 2-month aggregation test — re-ran the full `test_payroll.py` acceptance suite (18 checks) after all of Phase 10's changes to confirm nothing regressed. All pass.
- **Historical-data question (Q7)** — resolved by observation, not by asking: the live database has **zero** existing `payroll_runs`/`payroll_salary_structures` rows (confirmed by direct query). There is no historical payroll data to migrate — this deployment starts clean by necessity, not by choice, which was the simpler of the two original options anyway.
- **Documentation** — `README.md` updated: a new **Payroll** feature-table row and full section (design principles, page-by-page breakdown, fiscal-year model, salary-head/deduction model, how generation works), the **Database Schema** section extended with all 10 new/changed payroll tables, the **Audit Log** "Covered tables" list brought up to date (was still listing only the original 4 payroll tables from Phase 13), and **Project Structure** updated with `attendance_engine.py`, `payroll.py`, `payroll_plan.md`, `test_payroll.py`, and every new payroll template.

This plan document itself (`payroll_plan.md`) remains the authoritative record of every decision made along the way — including the ones later phases changed course on (e.g. the attendance-pipeline unification decision in Section 8, settled by your explicit instruction rather than left as Section 3's original open choice).

---

## 0. What already exists (so we extend, not rebuild)

The repo already has a working payroll module (Phase 13/14 of the original build), wired into the nav and RBAC:

- `payroll.py` — pure tax/OT math: Nepal FY2081/82 slabs (single & married), cumulative TDS with mid-year true-up, hourly-rate/OT-amount helpers, `compute_payslip()`.
- `db.py` — tables `payroll_salary_structures`, `payroll_runs`, `payroll_items`, `payroll_holiday_ot_rules`, plus helpers (`get_salary_structure`, `create_payroll_run`, `insert_payroll_item`, `get_month_present_days`, `get_month_ot_split`, `get_ytd_tax_totals`, …).
- `web/app.py:5562-5899` — routes: `/payroll` (run list), `/payroll/salary-structures`, `/payroll/runs/new`, `/payroll/runs/generate`, `/payroll/runs/{id}`, `/payroll/runs/{id}/item/{gu_id}`, `/payroll/runs/{id}/payslip/{gu_id}`, `/payroll/tax-preview`, `/payroll/holiday-ot-rules`.
- Templates: `payroll_home.html`, `payroll_salary_structures.html`, `payroll_run_new.html`, `payroll_run.html`, `payroll_payslip.html`, `payroll_holiday_ot_rules.html`.
- Already admin-only via `_ADMIN_ONLY_PREFIXES` in `web/app.py:152-155` (prefix `/payroll`), already in the sidebar nav (`base.html:165-186`), already wired into the generic audit-log trigger (`_AUDITED_TABLES` in `db.py:790-796`).

**Gaps versus what your sheet and your latest instructions need** (this plan exists to close these):

1. `payroll_salary_structures` is flat — one `basic_salary` + one `allowances` number. Your sheet has 12 distinct heads (Basic, Ad.10%, Upadan, Allowance, Tiffin, Medical, OT, Dress, Dashain, Copy, Barshik, Rahat), several percent-of-basic, several annual/one-time, not monthly.
2. No retirement deduction (PF), no CIT, no insurance anywhere in the codebase — `compute_payslip()` goes straight from gross to tax with no pre-tax deduction step.
3. **Everything tax-related is hardcoded**: the tax slab tables are a Python dict (`payroll.py:29-44`, `_SLABS`), the fiscal-year start month is a hardcoded `4` (`payroll.py:100`, `fiscal_period_index`), and the default shift window used across all attendance reports is `SI_MIN, SO_MIN = 600, 1020` copy-pasted in three places (`web/app.py:3316`, `3437`, `3598`). None of this is admin-editable — every change needs a code deploy. **This is also a live correctness bug, not just a rigidity problem**: the current `_SLABS` dict has no top band above its last tier — `single` stops at "36% on everything above 2,000,000" and `married` stops at "36% on everything above 1,100,000," with no 39%-above-5,000,000 tier at all. Every published rate table you've supplied (2082/83 and the "2026 update") shows a fifth, higher band the running code doesn't have. This alone means current payroll output is wrong for any employee whose income reaches the top bracket, independent of anything else in this plan.
4. **Tax slabs are not fiscal-year-aware at all.** Nepal's slabs change nearly every budget — you've supplied three different rate structures for three different periods (2082/83, a "2026 update," and the proposed FY2083/84 unified structure that removes the single/married split entirely and drops the top rate to 29%). The system needs to hold *multiple* slab sets simultaneously, pick the right one by the payroll run's fiscal year, and — critically — the FY2083/84 structure is reportedly **unified** (one table, no marital-status split), which the current schema design (Section 6.1) must be able to represent without a special case.
5. `company_settings.fiscal_year_bs` (`db.py:574`) already exists as a column and is already editable from `/settings` (`web/app.py:4121-4140`), **but nothing in the codebase ever reads it** (confirmed by grep — it's a write-only display label today). Fiscal-year logic elsewhere is independently hardcoded instead of using this single field.
6. `compute_payslip()`'s deductions are subtracted **after** tax, not before — your sheet requires Retirement + CIT + Insurance to reduce taxable income **before** the slab calculation (`payroll.py:213-215` currently computes `this_month_taxable = gross`, ignoring deductions entirely). Some of the deduction figures you've now supplied (insurance caps of ₨40,000 life / ₨20,000 health, retirement-fund deduction capped at the lesser of ⅓ of gross or ₨500,000/year) are **caps on the deduction**, not the deduction amount itself — the schema needs to represent a cap separate from the actual contribution (Section 6.2).
7. No annual roll-up report that reconciles 12 monthly runs against one fiscal-year summary in your sheet's exact shape, and no report shows the underlying **formula** (what was added, what was subtracted) — only final numbers.
8. **Identity is fragmented.** `global_users` alone carries three different ID-like fields: `id` (internal integer PK, surfaced as "Global ID" in reports), `global_user_id` (VARCHAR, surfaced as "Employee Id"/`company_id`, originally the ZKTeco device sync ID), and `employee_id` (VARCHAR, added later — `db.py:315`, indexed at `329`, used for search/sort at `1386/1410/1434-1435/1455/3396` — this looks like it was meant to be the real HR/master employee code but is inconsistently used). Separately, `attendance_logs.employee_id` is an **integer FK to `employees(id)`** — a completely different table, the device-linked record, not `global_users`. Four different things are called "employee_id" or "ID" across the codebase. This needs to be resolved into one canonical identity before payroll builds more tables on top of it (Section 3).
9. Nepali-calendar (BS) date pairing exists but is inconsistent — some tables have `_bs` companions for every AD date (`created_bs`/`updated_bs`, `holiday_bs`, `from_bs`/`to_bs`, `applied_bs`, `bs_date`), others don't. New payroll tables need to follow the established pattern uniformly (Section 4).
10. Attendance→payroll linkage currently reads from `attendance_daily`, a **separate pre-aggregated pipeline** from the live `_compute_monthly_report()`/`_monthly_totals()` used by every attendance report (`/reports/monthly/view`, `/reports/monthly/print-all`, `/reports/hajiri`). These two pipelines can disagree. **You've now explicitly directed that payroll must use the live report pipeline** — Section 8 covers how.
11. Nothing about payroll is persisted as a point-in-time record for later comparison — if attendance is corrected after a payroll run, there's no stored "what the numbers were when this payslip was generated" snapshot to diff against. You asked for this explicitly ("per month days working and all, so we can find change log in future") — Section 8 covers the new snapshot table.
12. No uniform search/filter/PDF/Excel treatment across reports — some reports have Excel export, some don't; none of the payroll pages have it yet.
13. No bulk "generate/print/download all payslips" for a run — only one employee at a time.
14. **No mechanism to close/lock a fiscal year.** Nepal payroll practice (per the rollover checklist you supplied) requires the old year's final run to be locked before the new year's slabs go live, so nobody edits a closed period after eTDS has been filed against it. `payroll_runs` has no lock/status concept beyond a `status='draft'` default that's never changed (Section 6.4).

The tax slab **numbers** currently coded in `payroll.py` (`"married"`: 600,000 @ 1%, next 200,000 @ 10%, next 300,000 @ 20%, next 900,000 @ 30%, remainder @ 36%) are close to — but not identical to — your sheet's Step 6 result (₨19,358.35 on ₨733,583.48 taxable, which only exercises the first two bands and so doesn't reveal the missing top band). The *shape* of the calculation (cumulative band-by-band) is right; the specific rate table needs to become versioned, admin-entered data rather than a hardcoded dict, exactly because it's already both incomplete (#3) and about to change again for FY2083/84 (#4).

---

## 1. Guiding principles for this build

These apply to every phase below, not just one section — restating them here since they came from your explicit instructions:

- **Nothing hardcoded.** Tax slabs, fiscal-year start month, default shift window, PF %, CIT amount, insurance amount, the salary-head catalog itself — all live in the database, editable by an admin, versioned by effective date. Python code reads config; it never encodes policy values.
- **One fiscal year setting, used everywhere.** A single config value drives every fiscal-year-aware calculation (TDS true-up period index, annual summary boundaries, YTD tax aggregation).
- **One identity model, used everywhere.** A single "master employee ID" concept, carried consistently onto every new table this plan creates, and reconciled with the existing `global_users.id` / `global_user_id` / `employee_id` / `attendance_logs.employee_id` split described above.
- **Nepali calendar on every date.** Every new date column gets a paired BS string column, following the existing convention.
- **One attendance pipeline.** Payroll consumes exactly the same computation the attendance reports show a user — no second, silently-divergent source of truth.
- **Everything persisted and audited.** Generated payroll numbers (attendance snapshot, head breakdown, deductions, tax) are written to the database at generation time, not recomputed-and-discarded, and every table involved is covered by the existing generic audit trigger so corrections leave a change-log trail.
- **Uniform report UX.** Every report — new or existing — gets the same search/filter, PDF download, and Excel export/import treatment, in the same visual format already established this session for the monthly attendance print-all report.
- **Formula transparency.** Payslips and the annual summary show the arithmetic (heads added, deductions subtracted, slab bands applied), not just final numbers.

---

## 2. Open questions (please answer before Phase 1 starts)

1. **PF split** — is the 20% of Basic entirely an employee deduction, or split employee/employer (e.g. 10%/10%, or 11% employee + 20% employer as under Nepal's SSF)? Any employer-side portion should be tracked but not deducted from the employee's net pay.
2. **CIT (₨36,000/yr)** — same flat amount for every employee, or does it vary per person? Mandatory or opt-in?
3. **Insurance (₨40,000/yr)** — flat company policy amount, or actual premium that varies per employee/plan?
4. **One-time/annual heads (Dress, Dashain, Copy, Barshik, Rahat)** — paid in the same BS month every year for everyone, or does an admin pick the month per run? Same amount for every employee/grade, or does it vary?
5. **Unpaid leave & absence** — should they reduce the Basic-and-head proration proportionally (matching how `compute_payslip()` already prorates by `present_days/working_days`)?
6. **Marital status** — `payroll_salary_structures.marital` already exists and drives which tax-slab table is used. Confirm this stays the source of truth per employee.
7. **Historical data** — do last year's monthly payslips (if any exist) need migrating/recomputing into the new model, or does this start clean from the next run?
8. **Report audience** — Annual Payroll Summary: admin-only (matches current `/payroll` RBAC), or should individual employees also see their own annual summary via a `/my-attendance`-style self-service page?
9. **Fiscal year start month** — confirm it's Shrawan (BS month 4) for your company, matching the current hardcoded assumption, so Section 5's config default is seeded correctly.
10. **Master employee ID rollout** — `global_users.employee_id` exists today but isn't consistently populated/used. Is it already the intended official HR employee code (so we just start using it consistently), or does it need a data-cleanup pass first (duplicates/blanks) before it can be trusted as the master ID?
11. **`global_user_id` vs `employee_id`** — once `employee_id` becomes the master ID, does `global_user_id` (the original ZKTeco device-sync ID) stay as a separate "device sync ID" field forever, or should the two eventually be unified? (This plan assumes they stay separate — one is a sync/integration key, the other is the HR-facing ID — but flagging it since it affects how many report columns show both.)
12. **FY2083/84 slab source of truth** — the figures you pasted describe the *announced budget* structure (unified, no single/married split, top rate 29%) but the source text itself says to confirm against the enacted Finance Act and IRD guidance before running payroll. Who signs off on the exact FY2083/84 numbers entered into the system, and when (before or after Shrawan 1)? This plan will build the *capability* to hold a new slab set per fiscal year (Section 6.1); it will not itself decide what FY2083/84's numbers are.
13. **Unified vs split structure for FY2083/84** — assuming it's confirmed unified (no marital-status distinction), does `payroll_salary_structures.marital` become irrelevant for that year's tax calc (kept only for historical years), or does the company still want to record it for other reasons?
14. **SSF 1% band treatment** — the source text explicitly flags this as unresolved ("reconcile... the treatment of the 1% band for SSF contributors"). Does the company's SSF/PF enrollment change how the first tax band applies for enrolled employees? This affects whether `payroll_deduction_types`/slab resolution need to interact (Section 6.2/6.1) or are fully independent.
15. **Deduction caps** — you've cited specific caps (retirement: lesser of ⅓ of gross or ₨500,000/yr; life insurance: ₨40,000/yr; health insurance: ₨20,000/yr). Should these be enforced automatically by the system (compute the deduction, then clamp to the cap and warn if clamped), or only used as guardrails an admin checks manually when setting up `payroll_employee_deductions`?
16. **Fiscal-year rollover timing** — do you want the "lock the old year" step (Section 6.4) to be a hard system lock (no edits possible, even by an admin, without explicitly unlocking) or a soft warning?
17. **`closed` vs `locked` distinction** — Section 5.2 proposes two separate statuses (a reconciliation-window `closed` state, then a terminal `locked` state). Do you want both, or is a single "closed = locked immediately" status simpler for how your team actually works?
18. **`active → upcoming` transition trigger** — should the new fiscal year activate itself automatically the moment today's AD date crosses into its `start_ad` (Shrawan 1), or must an admin explicitly flip it, so a forgotten activation never silently blocks payroll generation on day one of the new year?

---

## 3. Identity & audit unification

### 3.1 Current state (the four "IDs")

| Field | Table | Type | What it actually is today | Where shown |
|---|---|---|---|---|
| `id` | `global_users` | internal int PK | Database row identity | Reports label it "Global ID" |
| `global_user_id` | `global_users` | VARCHAR | Originally the ZKTeco device sync identifier | Reports label it "Employee Id" (`company_id`) |
| `employee_id` | `global_users` | VARCHAR, indexed | Added later (`db.py:315`), used in search/sort, intended as the real HR employee code | Not consistently surfaced in reports/payroll yet |
| `employee_id` | `attendance_logs` | integer FK → `employees(id)` | A *different* concept entirely — links a punch row to the device-level `employees` record, not to `global_users` | Internal join key only |

### 3.2 Proposal — one master ID, used consistently going forward

- **`global_users.employee_id` becomes the Master Employee ID** for every table this plan adds (payroll heads, deductions, snapshots, tax slabs' per-employee links, audit displays). This is the "introduce masterid from employee table... we will use it in future" ask.
- **`global_users.id` stays the relational FK** used in every foreign key (`global_user_id INTEGER REFERENCES global_users(id)`) — this doesn't change, it's how every existing table already joins. The master employee ID is *displayed and searched on*, not used as the FK type, to avoid a breaking schema change across the whole system.
- Every new table in this plan that is employee-scoped stores **both**: the relational FK (`global_user_id INTEGER REFERENCES global_users(id)`) and, where the row represents a point-in-time record (payslip, snapshot, payroll item), a **denormalized snapshot** of the master `employee_id` string and employee name *as of generation time* — so if an employee's HR code is corrected six months later, historical payslips still show what was true when they were issued. This mirrors how `payroll_items` should never silently change historical numbers.
- `attendance_logs.employee_id` (the device-linked FK) is left as-is — it's a different, correct concept (device record linkage) and already resolves to `global_users` via `employees.global_user_id`. The "uniform attendance ID" ask is satisfied by always resolving through `global_users.id` at the reporting/payroll layer (never joining on raw device `employees.id` directly in new code), which is already the pattern `get_employees_for_report()` and `_compute_monthly_report()` use.
- Reports/payslips going forward always display **both** the Master Employee ID (`employee_id`) and the internal Global ID (`id`) side by side, consistent with how the monthly report already shows "ID" and "Global ID" separately (`reports_monthly.html:231-232`) — just wired to the correct source column once #3.1 is resolved (today "ID" actually shows `global_user_id`, not `employee_id` — Phase 2 corrects this).

### 3.3 Audit checklist (applied in Phase 2, re-verified at the end of every later phase)

For every table this plan creates or touches:

| Requirement | How it's verified |
|---|---|
| Carries `global_user_id` FK where employee-scoped | Schema review |
| Carries denormalized master `employee_id` + name where the row is a point-in-time record | Schema review |
| Has paired AD+BS columns for every date field | Schema review (Section 4) |
| Registered in `_AUDITED_TABLES` (`db.py:790-796`) with the trigger attached | `SELECT * FROM audit_log WHERE table_name = '<new table>'` after a test write |

---

## 4. Nepali calendar (BS) standardization

The codebase already has the right convention in multiple places — this plan just applies it uniformly to everything new:

- Every new date column `<field>_ad DATE` gets a companion `<field>_bs VARCHAR(10)`, populated via `nepali_utils.ad_to_bs()` at write time (same helper already used throughout `db.py`/`app.py`).
- Every BS month/year picker (payroll run period, annual summary period, one-time head `pay_bs_month`) uses `nepali_utils.bs_month_info()` for range resolution — the same function `/payroll/runs/new` already calls (`web/app.py:5643, 5663`) and every attendance report uses.
- The generic audit trigger (`fn_audit_log()`, `db.py:749-787`) snapshots the **full row** as JSONB on every change, so once a table has proper BS columns, the audit log automatically captures BS-date history too — no extra audit work needed beyond having the columns exist.

---

## 5. Fiscal year — a real table, not a config flag

**Revised per your latest instruction** — a single `fiscal_year_start_month` setting (the original draft of this section) is not enough on its own: you want each fiscal year to be an explicit, admin-managed record with its own AD *and* BS start/end dates, a status, and a change log — since "the user will change the fiscal year" (i.e. an admin opens each new year deliberately, doesn't just let a constant roll over silently), and because BS month lengths vary year to year (29–32 days), so "the last day of Ashadh" isn't a fixed number you can hardcode — it has to be computed per year.

- **Current state**: `company_settings.fiscal_year_bs` (`db.py:574`) exists and is editable via `/settings` (`web/app.py:4121-4140`), but it's a free-text label nothing ever reads back. Meanwhile `payroll.fiscal_period_index()` (`payroll.py:95-100`) hardcodes `((bs_month - 4) % 12) + 1`, baking in "fiscal year starts in Shrawan" as a Python constant with no stored start/end dates anywhere.

### 5.1 New table: `fiscal_years`

One row per fiscal year, e.g. the two you gave as examples:

| `fiscal_year_bs` | `start_bs` | `end_bs` | `start_ad` | `end_ad` |
|---|---|---|---|---|
| `2082/83` | `2082-04-01` | `2083-03-XX`* | `2025-07-17` | `2026-07-16` |
| `2083/84` | `2083-04-01` | `2084-03-XX`* | `2026-07-17` | `2027-07-16` |

*\*The last day of BS month 3 (Ashadh) is **not a fixed number** — Nepali calendar months run 29–32 days and vary year to year. `end_bs`/`end_ad` are computed at the point a fiscal year is created by calling `nepali_utils.bs_month_info(end_year, 3)`, which already returns exactly this (`days` = the month's actual length that year, `last_ad` = the AD date of that last day) — never hardcoded as "32."*

| column | notes |
|---|---|
| `id` | PK |
| `fiscal_year_bs` | e.g. `2082/83` — unique, human label |
| `start_bs`, `end_bs` | VARCHAR(10) BS dates — always BS month 4 day 1 through BS month 3 of the following year's last day, per Nepal's standard fiscal calendar (Shrawan 1 → Ashadh-end) |
| `start_ad`, `end_ad` | DATE — the AD equivalents, computed via `nepali_utils.bs_to_ad()`/`bs_month_info()` at creation time, stored (not recomputed on every read) |
| `status` | `upcoming` / `active` / `closed` / `locked` — see 5.2 |
| `created_by`, `created_at`, `updated_by`, `updated_at` | standard audit columns |

Added to `_AUDITED_TABLES` — every creation and every status transition (`upcoming → active`, `active → closed`, `closed → locked`) is a row in the generic `audit_log` with old/new status, who did it, and when, in both AD and BS (the trigger already captures `changed_at TIMESTAMPTZ`; a BS-formatted view of that timestamp is a one-line `nepali_utils` call in the audit-log UI, not a new column).

### 5.2 Status lifecycle

- **`upcoming`** — created ahead of time (e.g. an admin sets up FY2083/84 in Chaitra of FY2082/83, once that year's confirmed tax slabs are entered per Section 6.1), but not yet the operative year. No payroll runs may target it while `upcoming`.
- **`active`** — exactly one `fiscal_years` row has this status at a time; this is the year new payroll runs default to. Transition `upcoming → active` happens on or after `start_ad` (Shrawan 1), either by an admin action or a scheduled check — your call in Section 2's open questions.
- **`closed`** — the year's final (Ashadh) run has been generated and reconciled; new runs can no longer be created against it, but it's not yet fully locked (a correction window).
- **`locked`** — hard lock (Section 6.4): no further writes to any `payroll_runs`/`payroll_items`/`payroll_attendance_snapshot` row belonging to this fiscal year, enforced at the application layer. This is the terminal state matching "Lock the old period so no one edits a closed year."

This status column is what Section 6.4's rollover procedure actually operates on — that section is revised below to reference `fiscal_years.status` directly instead of a standalone `payroll_runs.is_locked` flag.

### 5.3 One shared resolver, used everywhere

A single helper, `get_fiscal_year_for(conn, ad_date=None, bs_date=None)`, is the **only** place fiscal-year boundaries are resolved from — it looks up the `fiscal_years` row whose `[start_ad, end_ad]` (or `[start_bs, end_bs]`) range contains the given date, defaulting to today. Every fiscal-year-aware calculation calls it instead of re-deriving Shrawan-based math:

- `fiscal_period_index()` in `payroll.py` — currently hardcodes month-4-start math; becomes "how many BS months since this fiscal year's `start_bs`," computed from the resolved row instead of a constant.
- Annual Payroll Summary report's period boundaries (Section 9).
- `get_ytd_tax_totals()`'s year-window logic (`db.py:4193-4206`, currently inlines the same Shrawan assumption via `bs_month >= 4` / `<= 3`).
- `payroll_tax_slab_sets` (Section 6.1) — its `fiscal_year_bs` free-text column becomes a proper `fiscal_year_id` FK into this table instead of a duplicated label.
- `payroll_runs` — gains a `fiscal_year_id` FK (in addition to its existing `bs_year`/`bs_month`), so a run's lock state is a direct join to `fiscal_years.status` rather than inferred from BS year/month arithmetic.

`company_settings.fiscal_year_bs` (the old free-text field) is superseded by "whichever `fiscal_years` row has `status = 'active'`" — kept as a column for backward display compatibility but no longer authoritative once this ships.

---

## 6. Tax calculation — fully dynamic, per-employee setup

This directly answers "add tax calculation plan and setup to deduce in each employee, properly so everything are managed."

### 6.1 Tax slabs become data, not code — scoped by fiscal year, not just a date

This is the direct answer to "slabs are fiscal year oriented, it should be saved in the system then hardcoding... based on the nepali fiscal year each year slabs are different."

New table `payroll_tax_slab_sets` — one row per **fiscal-year variant**, so a year with a single/married split has two rows and a unified year (like the proposed FY2083/84) has one:

| column | notes |
|---|---|
| `id` | PK |
| `fiscal_year_id` | FK → `fiscal_years` (Section 5.1) — the FY this set applies to. Replaces a free-text label with a real relationship, so a slab set can never reference a fiscal year that doesn't formally exist yet. |
| `marital_status` | `single` / `married` / **`ALL`** — `ALL` means this fiscal year doesn't split by marital status (the FY2083/84 unified structure). Slab resolution (6.3) looks for an exact marital-status match first, falling back to `ALL` if no split exists for that fiscal year. This is what lets a unified year and a split year coexist in the same table without a schema change. |
| `is_ssf_adjusted` | bool — whether this variant already folds in the 1%-band SSF treatment mentioned in your source material, vs. a non-SSF variant. Kept as an explicit flag rather than an assumption baked into the rate itself, since you've flagged this specific point as still needing IRD confirmation (Section 2, Q14). |
| `source_note` | free text — e.g. "Budget announcement, pending Finance Act confirmation" or "Confirmed per IRD circular dated ..." — an audit trail of *why* these numbers were trusted, separate from the generic audit log's who/when. |
| `is_confirmed` | bool, default `false` — a slab set entered from a budget announcement starts unconfirmed; payroll generation (6.3) refuses to run against an unconfirmed set for a *current or future* period and shows a warning banner, forcing an explicit admin confirmation step once the Finance Act / IRD guidance is in hand. This directly implements "Confirm before you run payroll" from your source material as a system control, not just documentation. |
| `created_by`, `created_at` | audit |

No separate `effective_from_bs`/`effective_to_bs` is needed on the slab set itself — it inherits its validity window directly from `fiscal_years.start_bs`/`end_bs` via the FK, one less place for dates to drift out of sync.

New table `payroll_tax_slab_bands` — the actual bands, child of a slab set:

| column | notes |
|---|---|
| `slab_set_id` | FK → `payroll_tax_slab_sets` |
| `band_order` | 1..N, evaluated in order |
| `band_width` | Rs width of this band; `NULL` = "remainder" (the top open-ended band) — **this column existing at all fixes the current bug** where `_SLABS` silently has no top band |
| `rate_percent` | e.g. `1.00`, `10.00`, `20.00`, `27.00`, `29.00` |

Seeded, on Phase 1, with the **currently-running FY's actual values** (not the pasted FY2083/84 figures) — corrected to include the missing top band per the rate tables you supplied, and entered as `is_confirmed = true` since that's the year already in production use. Any future fiscal year (including FY2083/84) is added as a **new, separately-confirmed** `payroll_tax_slab_sets` row before that year's first payroll run — never by editing/overwriting the prior year's rows, since historical payslips must keep resolving against the slab set that was actually in force when they were generated (mirrors the point-in-time snapshot principle in Section 3.2/8.1).

`payroll.annual_tax()`, `slab_breakdown()`, and `monthly_tds()` change signature to accept the resolved band list as a parameter (the caller queries `payroll_tax_slab_bands` joined to the correct `payroll_tax_slab_sets` row for the run's fiscal year + marital status) instead of reading the module constant — the functions themselves stay pure and unit-testable, just fed data instead of a baked-in dict.

### 6.2 Per-employee deduction setup — generalized, not three fixed columns

Rather than hardcoding PF/CIT/Insurance as three fixed columns forever (which would just move the "hardcoded" problem from Python into a rigid schema), model them the same extensible way as salary heads:

New table `payroll_deduction_types` (company-wide catalog):

| column | notes |
|---|---|
| `code` | `PF`, `CIT`, `INSURANCE`, extensible for future types |
| `name` | display label |
| `calc_type` | `fixed` or `percent_of_basic` |
| `default_amount` / `default_percent` | starting value new employees inherit |
| `is_pretax` | bool — reduces taxable income before the slab calculation (PF/CIT/Insurance are all `true` per your sheet) |
| `frequency` | `monthly` / `annual` (annual amounts divide across the fiscal year the same way one-time earning heads do) |
| `is_active` | lifecycle |

New table `payroll_employee_deductions` (per-employee enrollment/override):

| column | notes |
|---|---|
| `global_user_id` | FK |
| `deduction_type_id` | FK → `payroll_deduction_types` |
| `is_enrolled` | bool |
| `amount` / `percent_override` | per-employee override of the catalog default (nullable = use catalog default) |
| `effective_bs` | when this enrollment/override started |
| `is_active` | lifecycle |

This becomes the **single "Tax & Statutory Deductions" admin screen per employee**: marital status (from `payroll_salary_structures.marital`, already exists), plus every enrolled deduction type with its resolved amount, in one place — exactly "setup to deduce in each employee, properly so everything are managed." Adding a 4th deduction type later (e.g. a loan repayment, a union due) is a catalog row, not a schema migration.

**Statutory caps** (retirement fund deduction capped at the lesser of ⅓ of gross income or ₨500,000/yr; life insurance capped at ₨40,000/yr; health insurance capped at ₨20,000/yr — per the figures you supplied) are represented on `payroll_deduction_types` as `cap_amount` and/or `cap_percent_of_gross` (nullable — most deduction types have no cap). At computation time (6.3), the *actual* contribution is computed first, then clamped to the cap if one is defined, and the clamp — if it fires — is recorded on the payslip's formula breakdown (Section 9) so it's visible when a deduction was reduced by a statutory limit rather than silently applied in full. Whether clamping is automatic or a manual admin check is Section 2, Q15 — this schema supports either answer.

### 6.3 Monthly tax computation, end to end

For each employee, each run:
1. Sum active `payroll_employee_deductions` where `is_pretax = true` (resolved amount, monthly-equivalent if `frequency='annual'`; capped per the `cap_amount`/`cap_percent_of_gross` rule on its `payroll_deduction_types` row) → `retirement_and_pretax_total`.
2. `this_month_taxable = gross - retirement_and_pretax_total` (the one-line fix to `compute_payslip()` described in the original plan, now sourced from dynamic per-employee data instead of two new hardcoded function args).
3. Resolve the run's fiscal year via `get_fiscal_year_for()` (Section 5.3) → find the matching `payroll_tax_slab_sets` row for that `fiscal_year_id` + the employee's `marital_status` (falling back to a `marital_status = 'ALL'` row if the fiscal year is unified, per 6.1) → load its `payroll_tax_slab_bands`.
4. If the resolved slab set has `is_confirmed = false`, **block generation** for that employee/run and surface a warning ("FY2083/84 tax slabs are not yet confirmed — see Section 6.1") rather than silently computing tax off an unconfirmed budget announcement.
5. If the resolved `fiscal_years.status` is `closed` or `locked`, **block generation entirely** — no payroll run may be created or edited against a closed/locked fiscal year (Section 6.4).
6. Run the existing cumulative-TDS true-up logic (`monthly_tds()`, unchanged algorithm) against the resolved bands.
7. Post-tax `other_deductions` (non-statutory, ad-hoc) still apply after tax, unchanged from today.

Every slab set, slab band, deduction type, and per-employee enrollment change is audited (added to `_AUDITED_TABLES`), so "why did this employee's tax change in month 7" — or "what were the FY2082/83 vs FY2083/84 rates" — is always answerable from `audit_log` and from `payroll_tax_slab_sets` directly.

### 6.4 Fiscal-year rollover: closing a year, opening the next

Directly addresses the rollover checklist you supplied, adapted to what this system needs to actually enforce (not the full HR-suite checklist — payroll/attendance-specific parts only), now built entirely on `fiscal_years.status` (Section 5.2) rather than a standalone flag:

- **Closing**: once the final (Ashadh) run of a fiscal year is generated and reconciled, an admin transitions that `fiscal_years` row from `active` → `closed`. Every `payroll_runs` row (linked via `fiscal_year_id`, Section 5.3) belonging to a `closed` or `locked` fiscal year is refused further writes at the application layer — matching "Lock the old period so no one edits a closed year." The transition itself, and who performed it and when (in both AD and BS, via the audit log), is captured by the same generic audit trigger already covering `fiscal_years`.
- **Locking**: a later, deliberate `closed` → `locked` transition (e.g. once eTDS has been filed and reconciled) makes the year's records permanently read-only — the terminal state. Whether `closed` alone is already effectively locked, or a genuinely separate hard-lock step is wanted, is Section 2's open question on rollover timing.
- **Opening a new fiscal year** = an admin creates the next `fiscal_years` row (Section 5.1 — start/end dates computed automatically via `nepali_utils.bs_month_info()`, status starts `upcoming`), adds a new, `is_confirmed = true` `payroll_tax_slab_sets` row for it once the year's rates are finalized (6.1), and reviews `payroll_employee_heads`/`payroll_employee_deductions` for the new year's salary changes. Transitioning `upcoming → active` on Shrawan 1 makes it the default target for new runs. No separate "opening balance" table is needed for payroll itself, since heads/deductions are already effective-dated (`effective_bs`) rather than reset annually. Leave-balance carry-forward is out of scope here — it's already handled by the existing `leave_balances.carried_forward` mechanism, unaffected by this plan.
- **Parallel-run verification**: the Phase 6 acceptance test (reproducing your reference sheet exactly) is the template for this — before transitioning a new fiscal year to `active` in production, generate one employee's payslip by hand against the confirmed official rates and compare to the system's output, the same way this plan's own acceptance test works.

---

## 7. Uniform report UX standard

Every report this plan touches — new or existing — gets the same treatment, matching the pattern already established for `/reports/monthly/print-all` this session and `/reports/daily/excel` for exports:

- **Search/filter toolbar**: employee/department/section/date-range filters, matching the `/reports/monthly` picker pattern.
- **Download PDF**: the per-card `html2pdf` worker-chain pattern (one page per employee, dark legible text, same A4-landscape print CSS) — not a fresh one-off implementation per report.
- **Excel export**: matching `/reports/daily/excel`'s pattern.
- **Excel upload + "Download Sample Template"** for any bulk-editable payroll data (e.g. bulk salary-head import), matching `/manual-attendance/sample`'s existing pattern.

Applies to: the new Attendance-for-Payroll summary report, the new Annual Payroll Summary report, and retrofitted onto the existing `/payroll` run list/payslip view where missing.

---

## 8. Attendance pipeline — unify on the live compute, persist a snapshot per run

**Decision (previously an open question, now settled by your explicit instruction): payroll uses the same live pipeline as the attendance reports.**

- `_compute_monthly_report()` + `_monthly_totals()` (currently defined inline in `web/app.py:2993-3165`) move into a shared module — e.g. a new `attendance_engine.py` at repo root, alongside `payroll.py` — so both the report routes (`/reports/monthly/view`, `/reports/monthly/print-all`, `/reports/hajiri`) and the payroll generation code import the **identical function**. No copy-pasted logic, no risk of the two silently diverging.
- The hardcoded `SI_MIN, SO_MIN = 600, 1020` (three call sites today) becomes one dynamic default read from `company_settings` (new columns, or a designated row in the existing `shifts` table) via a single helper — not copy-pasted a fourth time into payroll code.
- Payroll's existing `attendance_daily`-based helpers (`get_month_present_days`, `get_month_ot_split` in `db.py:4209-4267`) are **retired** in favor of calling the shared `attendance_engine` function directly, exactly as the attendance reports already do — this guarantees a payslip's present/absent/OT numbers always match what `/reports/monthly/view` shows for the same employee/month.
- **Paid-leave fix carried over from the original plan**: `paid_days = present_days + paid_leave_days` (paid leave = `attendance_daily`/live-day `remark == 'Leave'` where the underlying `leave_types.is_paid = TRUE`), used for proration instead of raw present-days-only.

### 8.1 Persisted per-run snapshot (new — "save this so we can find change log in future")

New table `payroll_attendance_snapshot`, written once per employee per payroll run at generation time:

| column | notes |
|---|---|
| `run_id` | FK → `payroll_runs` |
| `global_user_id` | FK |
| `employee_id_snapshot` | master employee ID, denormalized (Section 3.2) |
| `working_days`, `present_days`, `paid_leave_days`, `unpaid_leave_days`, `absent_days`, `weekend_days`, `holiday_days`, `festival_days` | day counts from `_monthly_totals()` |
| `total_work_minutes`, `regular_ot_minutes`, `holiday_ot_minutes`, `late_in_minutes`, `early_out_minutes` | time totals |
| `computed_at` | timestamp |

This is the persisted "per month days working and all" record — not a live recomputation that vanishes. It's added to `_AUDITED_TABLES`, so if an admin later corrects a punch and **regenerates** the run, the old snapshot row's UPDATE is captured with old/new values in `audit_log` — a genuine change log, answering the "so we can find change log in future" requirement directly.

The Attendance-for-Payroll summary report (below) becomes: a query over `payroll_attendance_snapshot` for months that already have a generated run, and a **live preview** (calling the same shared `attendance_engine` function, not yet persisted) for a month that hasn't been run yet — so admins can review before generating.

---

## 9. Payslip & annual report formula transparency

Every payslip and the Annual Payroll Summary shows the actual arithmetic, not just final numbers — for example:

```
Basic 47,086.33 + DA 4,708.63 + Upadan 2,825.18 + Allowance 2,000.00
  + Tiffin 1,020.83 + Medical 6,407.60 = Gross (before OT) 64,048.57
Gross 64,048.57 + OT 3,106.72 = Gross Income (this month)

Retirement (PF 20% of Basic ÷ 12) 9,417.27 + CIT (36,000 ÷ 12) 3,000.00
  = Pre-tax deductions 12,417.27
Gross − Pre-tax deductions − Insurance (40,000 ÷ 12) = Taxable this month

First 600,000 × 1% = 6,000.00
Remaining 133,583.48 × 10% = 13,358.35
Total annual tax = 19,358.35 → this month's TDS (cumulative true-up) = ...
```

`compute_payslip()` and the new slab functions already compute every one of these intermediate values internally — this is a presentation change (return the intermediate values in the result dict as a structured `formula_lines` list instead of discarding them), not new math. Both the payslip template and the Annual Payroll Summary template render this as a "How this was calculated" panel.

---

## 10. Bulk payslip generation

New "Print All / Download All Payslips (PDF)" action on a payroll run's page, reusing the exact per-card `html2pdf` worker-chain pattern, dark-text styling, and off-day/absent-day visual treatment already built for `reports_monthly_print_all.html` this session — one payslip per employee per PDF page, same visual format as the single-payslip view, same company header/footer.

- Route: `GET /payroll/runs/{run_id}/payslips/print-all`
- Template: `payroll_payslip_print_all.html`, structurally mirroring `reports_monthly_print_all.html` (`.screen-actions` bar, `.emp-block`/`.report-card` pattern, `pdf-export` class toggling, sequential per-card PDF rendering for reliability at scale).

---

## 11. Phases

Each phase ships independently and is reviewable/testable on its own before moving to the next. Renumbered from the original draft to front-load the "make everything dynamic" foundation, since later phases depend on it.

### Phase 1 — Foundational dynamic configuration
- New `fiscal_years` table (Section 5.1) + `get_fiscal_year_for()` helper (Section 5.3). Seeded with the currently-running fiscal year (status `active`) computed via `nepali_utils.bs_month_info()` for its real start/end AD+BS dates — not hardcoded. Added to `_AUDITED_TABLES`.
- `payroll_tax_slab_sets` + `payroll_tax_slab_bands` tables (Section 6.1), `fiscal_year_id` FK'd to the seeded row above, seeded with the **currently-running fiscal year's** rates — corrected to include the missing top band (Section 0, gap #3) — marked `is_confirmed = true`. Refactor `payroll.py`'s `annual_tax`/`slab_breakdown`/`monthly_tds` to accept a resolved band list as a parameter instead of reading `_SLABS`.
- Default shift window (`SI_MIN`/`SO_MIN` replacement) sourced from `company_settings`/`shifts` instead of the 3 hardcoded literals in `web/app.py`.
- `payroll_runs.fiscal_year_id` FK added (Section 5.3) so a run's lock state resolves through `fiscal_years.status` (Section 6.4) — no separate lock flag needed.
- Behavior change from today is intentional and isolated to the top-band fix — verified by a unit test asserting the corrected slabs against the rate tables you supplied, and by confirming every *other* tax-preview output (below the old top band) is unchanged before/after.

### Phase 2 — Identity & audit unification
- Apply the Section 3.3 checklist to every existing payroll table; add missing `employee_id` denormalization where a table is a point-in-time record.
- Correct report templates that currently mislabel `global_user_id` as "ID" (Section 3.2) to actually show the master `employee_id`.
- No new user-facing features — a correctness pass.

### Phase 3 — Nepali calendar standardization
- Audit every table this plan will add in later phases against Section 4's convention before those phases start (done alongside each phase's schema work, not as a separate migration).

### Phase 4 — Salary head schema
- `payroll_heads` (catalog) + `payroll_employee_heads` (per-employee amounts/frequency/`pay_bs_month`), seeded with your 12 heads.
- One-off migration: existing `payroll_salary_structures.basic_salary`/`allowances` rows become `BASIC`/`ALLOWANCE` head rows so nothing configured today is lost.
- Add new tables to `_AUDITED_TABLES`.

### Phase 5 — Deduction/tax setup schema
- `payroll_deduction_types` (with `cap_amount`/`cap_percent_of_gross`) + `payroll_employee_deductions` (Section 6.2), seeded with PF/CIT/Insurance and their statutory caps.
- New "Tax & Statutory Deductions" admin screen per employee.

### Phase 6 — Tax engine pre-tax wiring
- `compute_payslip()` subtracts resolved, cap-clamped pre-tax deductions before calling the now-DB-driven, fiscal-year-scoped slab calculation (Section 6.3), including the `is_confirmed` guard that blocks generation against an unconfirmed slab set.
- Acceptance unit test (new `test_payroll.py`) reproduces your reference sheet's exact numbers (Gross 922,590.68 → Taxable 733,583.48 → Tax 19,358.35) end to end through the dynamic (DB-sourced) slab/deduction path — this is the acceptance test for the whole plan, not just this phase.
- **Not included here**: entering FY2083/84's actual rates. That happens whenever Q12 (Section 2) is answered and the numbers are confirmed against the enacted Finance Act — at that point it's a data-entry action (a new `payroll_tax_slab_sets` + bands, per Section 6.1), not a code change, which is the entire point of this phase's work.

### Phase 7 — Attendance pipeline unification
- Extract `_compute_monthly_report`/`_monthly_totals` into shared `attendance_engine.py` (Section 8); update `web/app.py`'s report routes to import from there (no behavior change for existing reports).
- Retire payroll's `attendance_daily`-based helpers in favor of the shared function.
- New `payroll_attendance_snapshot` table (Section 8.1), written per run, audited.
- New Attendance-for-Payroll summary report (live preview + persisted snapshot query), with full search/filter/PDF/Excel per Section 7.

### Phase 8 — Payroll run generation using heads, deductions, and the snapshot
- `/payroll/runs/generate` sums `payroll_employee_heads` (monthly + due one-time/annual heads for that BS month) and `payroll_employee_deductions`, using `payroll_attendance_snapshot` for proration — replacing the flat `basic_salary + allowances` and the old attendance helpers.
- New child tables `payroll_item_heads` and `payroll_item_deductions` for immutable per-run, per-head/per-deduction breakdown (so a later catalog change never rewrites a historical payslip).
- Update `payroll_run.html`/`payroll_payslip.html` to render the head/deduction breakdown and the Section 9 formula panel.

### Phase 9 — New reports
- Annual Payroll Summary (Section 9's formula transparency, matches your sheet's layout, full search/filter/PDF/Excel).
- Bulk "Download All Payslips" for a run (Section 10).
- Both added to the Payroll sidebar section (`base.html:165-186`).

### Phase 10 — Management UI
- Everything from Phases 1, 4, 5 becomes editable by an admin without a deploy: salary heads, deduction types, tax slabs, default shift window, and a **Fiscal Years** admin screen (list/create/status-transition against `fiscal_years`, Section 5) — this is the concrete answer to "user will change the fiscal year."

### Phase 11 — Rollout & verification
- Side-by-side check against your reference sheet for a real employee, confirmed to the rupee.
- Resolve the historical-data question (Q7 in Section 2) — migrate or start clean.
- Update `AGENTS.md`/`CLAUDE.md`/`README.md` payroll section if one exists.

---

## 12. Files this will touch (once approved)

| File | Change |
|---|---|
| `db.py` | New tables across Phases 1–8 (`fiscal_years`, `payroll_tax_slab_sets`, `payroll_tax_slab_bands`, `payroll_heads`, `payroll_employee_heads`, `payroll_deduction_types`, `payroll_employee_deductions`, `payroll_attendance_snapshot`, `payroll_item_heads`, `payroll_item_deductions`); `payroll_runs.fiscal_year_id`; retirement of `attendance_daily`-based payroll helpers |
| `payroll.py` | Slab functions accept a resolved band list instead of `_SLABS`, fixing the missing-top-band bug; `compute_payslip()` pre-tax deduction + cap wiring; `fiscal_period_index()` reads config instead of hardcoding `4` |
| new `attendance_engine.py` | Extracted from `web/app.py:2993-3165`, shared by reports and payroll |
| `web/app.py` | Report routes import from `attendance_engine.py` instead of defining it inline; updated/new `/payroll/*` routes (Phases 6–10) |
| `web/templates/payroll_*.html` | Head/deduction breakdown, formula panel, bulk print-all, 2 new templates |
| `web/templates/base.html:165-186` | New nav links |
| new `test_payroll.py` | Acceptance test against your reference sheet |

No changes needed to the attendance report code's *behavior* — only its location (Phase 7 extraction) — since the whole point of Section 8 is that reports and payroll converge on one implementation.
