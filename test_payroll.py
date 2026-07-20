"""
Payroll tax-engine acceptance test — no DB required (payroll.py is pure).

Usage:
    python test_payroll.py

What it tests:
  1. The seeded married-status slab bands reproduce the user's reference
     sheet exactly (Taxable 733,583.48 -> Tax 19,358.35) — this only
     exercises the first two bands, so it's unaffected by the top-band fix
     and proves the DB-driven band math didn't regress the existing case.
  2. The top tax band, previously missing entirely from the hardcoded
     _SLABS dict, now applies correctly for high incomes (single & married).
  3. fiscal_period_index() honors a configurable fiscal-year start month
     instead of assuming Shrawan.
  4. compute_payslip() refuses to run without tax_bands (no silent
     hardcoded-default fallback).
  5. compute_payslip() now actually subtracts pre-tax deductions (PF/CIT/
     Insurance) from taxable income before the slab calculation — the core
     Phase 6 fix (previously taxable income was simply `gross`).
  6. Full 12-month simulation using PF/CIT/Insurance monthly figures that
     exactly match the reference sheet (PF 9,417.27/mo, CIT 3,000.00/mo)
     converges to the sheet's annual Taxable Income (733,583.48) and annual
     Tax (19,358.35) via the existing cumulative-TDS true-up — this is the
     end-to-end acceptance test for the whole plan, not just this phase.
"""
from decimal import Decimal

import payroll as pay

# Mirrors db._SEED_TAX_BANDS — the corrected slabs seeded on first install.
SINGLE_BANDS = [
    {"width": 500000, "rate": 1},
    {"width": 200000, "rate": 10},
    {"width": 300000, "rate": 20},
    {"width": 1000000, "rate": 30},
    {"width": 3000000, "rate": 36},
    {"width": None, "rate": 39},
]
MARRIED_BANDS = [
    {"width": 600000, "rate": 1},
    {"width": 200000, "rate": 10},
    {"width": 300000, "rate": 20},
    {"width": 900000, "rate": 30},
    {"width": 3000000, "rate": 36},
    {"width": None, "rate": 39},
]

failures = []


def check(label, actual, expected):
    ok = actual == expected
    print(f"  {'OK  ' if ok else 'FAIL'}  {label}: {actual} (expected {expected})")
    if not ok:
        failures.append(label)


print("\n" + "=" * 60)
print("  Payroll tax engine — acceptance test")
print("=" * 60)

# ── 1. Reference sheet (married, low band only) ─────────────────────────────
print("\n[1] Reference sheet reproduction (married, first two bands)")
tax = pay.annual_tax(Decimal("733583.48"), MARRIED_BANDS)
check("annual_tax(733583.48, married)", tax, Decimal("19358.35"))

breakdown = pay.slab_breakdown(Decimal("733583.48"), MARRIED_BANDS)
check("slab_breakdown band count", len(breakdown), 2)
check("band 1 tax (600000 @ 1%)", breakdown[0]["tax"], Decimal("6000.00"))
check("band 2 tax (133583.48 @ 10%)", breakdown[1]["tax"], Decimal("13358.35"))

# ── 2. Top band, missing from the old hardcoded _SLABS dict ─────────────────
print("\n[2] Top band fix (previously missing above the old cutoff)")
# Single: 500k@1 + 200k@10 + 300k@20 + 1,000,000@30 + 3,000,000@36 + 1,000,000@39
#       = 5,000 + 20,000 + 60,000 + 300,000 + 1,080,000 + 390,000 = 1,855,000
tax_single_high = pay.annual_tax(Decimal("6000000"), SINGLE_BANDS)
check("annual_tax(6,000,000, single) includes 39% top band", tax_single_high, Decimal("1855000.00"))

# Old hardcoded dict had single's last band as "36% on everything above
# 2,000,000" with no top band — that would have given 5000+20000+60000+
# 300000+ (6000000-2000000)*0.36 = 385000+1440000 = 1,825,000. Confirm the
# fix actually changes the answer (i.e. this isn't a no-op).
old_buggy_result = Decimal("5000") + Decimal("20000") + Decimal("60000") + Decimal("300000") \
    + (Decimal("6000000") - Decimal("2000000")) * Decimal("0.36")
check("top-band fix changes the answer vs. the old buggy calculation",
      tax_single_high != old_buggy_result, True)

# ── 3. Configurable fiscal-year start month ──────────────────────────────────
print("\n[3] fiscal_period_index() with a configurable start month")
check("Shrawan (month 4) is period 1 when FY starts in Shrawan",
      pay.fiscal_period_index(4, fiscal_start_month=4), 1)
check("Ashadh (month 3) is period 12 when FY starts in Shrawan",
      pay.fiscal_period_index(3, fiscal_start_month=4), 12)
check("default fiscal_start_month is still 4 (Shrawan) when unspecified",
      pay.fiscal_period_index(4), 1)

# ── 4. compute_payslip requires explicit tax_bands ───────────────────────────
print("\n[4] compute_payslip() refuses to run without tax_bands")
try:
    pay.compute_payslip(basic_salary=50000, working_days=30)
    check("compute_payslip() without tax_bands raises ValueError", False, True)
except ValueError:
    check("compute_payslip() without tax_bands raises ValueError", True, True)

# ── 5. Pre-tax deduction subtraction (the core Phase 6 fix) ──────────────────
print("\n[5] Pre-tax deductions now reduce taxable income before the slab calc")
slip_no_pretax = pay.compute_payslip(
    basic_salary=Decimal("47086.33"), allowances=Decimal("16962.24"),
    working_days=30, tax_bands=MARRIED_BANDS, period_index=1,
)
check("without pretax_deductions, taxable == gross (old behavior preserved)",
      slip_no_pretax["taxable_this_month"], slip_no_pretax["gross"])

pretax_detail = [
    {"code": "PF", "name": "Provident Fund", "amount": Decimal("9417.27")},
    {"code": "CIT", "name": "Citizen Investment Trust", "amount": Decimal("3000.00")},
    {"code": "INSURANCE", "name": "Life Insurance Premium", "amount": Decimal("3333.33")},
]
slip_with_pretax = pay.compute_payslip(
    basic_salary=Decimal("47086.33"), allowances=Decimal("16962.24"),
    working_days=30, tax_bands=MARRIED_BANDS, period_index=1,
    pretax_deductions=pretax_detail,
)
expected_pretax_total = Decimal("9417.27") + Decimal("3000.00") + Decimal("3333.33")
check("pretax_deductions_total sums the detail list",
      slip_with_pretax["pretax_deductions_total"], expected_pretax_total)
check("taxable_this_month = gross - pretax_total",
      slip_with_pretax["taxable_this_month"], slip_with_pretax["gross"] - expected_pretax_total)
check("gross itself is unaffected by pretax_deductions",
      slip_with_pretax["gross"], slip_no_pretax["gross"])
check("taxable income is strictly lower with pretax deductions applied",
      slip_with_pretax["taxable_this_month"] < slip_no_pretax["taxable_this_month"], True)

# ── 6. Full 12-month simulation against the reference sheet's annual totals ──
print("\n[6] 12-month simulation converges to the reference sheet's annual figures")
MONTHLY_RECURRING_GROSS = Decimal("64048.57")   # Basic+DA+Upadan+Allowance+Tiffin+Medical, matches sheet exactly
MONTHLY_PRETAX = [
    {"code": "PF", "name": "Provident Fund", "amount": Decimal("9417.27")},
    {"code": "CIT", "name": "Citizen Investment Trust", "amount": Decimal("3000.00")},
    {"code": "INSURANCE", "name": "Life Insurance Premium", "amount": Decimal("3333.33")},
]
# Yearly one-time benefits (Dashain+Dress+Copy+Barshik+Rahat) + annual OT,
# added as a single lump in the final period — Phase 8 will spread these
# across their actual pay_bs_month via the head engine; this test only
# needs the ANNUAL total to match, to prove the tax mechanic itself is right.
ANNUAL_GROSS_TARGET = Decimal("922590.68")
YEAR_END_LUMP = ANNUAL_GROSS_TARGET - (MONTHLY_RECURRING_GROSS * 12)

taxable_ytd = Decimal("0")
tax_paid = Decimal("0")
cumulative_gross = Decimal("0")
for period in range(1, 13):
    lump = YEAR_END_LUMP if period == 12 else Decimal("0")
    slip = pay.compute_payslip(
        basic_salary=Decimal("47086.33"), allowances=Decimal("16962.24"),
        other_earnings=lump, working_days=30, tax_bands=MARRIED_BANDS,
        period_index=period, pretax_deductions=MONTHLY_PRETAX,
        taxable_ytd_before=taxable_ytd, tax_paid_before=tax_paid,
    )
    cumulative_gross += slip["gross"]
    taxable_ytd = slip["taxable_ytd"]
    tax_paid += slip["tax"]

print(f"  Cumulative gross:   {cumulative_gross} (target {ANNUAL_GROSS_TARGET})")
print(f"  Cumulative taxable: {taxable_ytd} (reference sheet: 733583.48)")
print(f"  Cumulative tax:     {tax_paid} (reference sheet: 19358.35)")

# Allow a small rounding tolerance: monthly-installment division of annual
# figures (e.g. 40,000/12 = 3,333.33 x12 = 39,999.96, four paisa short of
# 40,000) is a disclosed, expected characteristic of spreading an annual
# amount across 12 rounded monthly deductions — not a bug. A real payroll
# run would true this up in the final period; this test just confirms the
# core mechanic lands within a few rupees of the sheet, not exactly to the
# paisa, since Phase 6 doesn't implement December true-up.
TOLERANCE = Decimal("5.00")
check("cumulative gross matches the reference sheet's annual gross",
      abs(cumulative_gross - ANNUAL_GROSS_TARGET) <= TOLERANCE, True)
check("cumulative taxable income matches the reference sheet (733,583.48) within tolerance",
      abs(taxable_ytd - Decimal("733583.48")) <= TOLERANCE, True)
check("cumulative annual tax matches the reference sheet (19,358.35) within tolerance",
      abs(tax_paid - Decimal("19358.35")) <= TOLERANCE, True)

print("\n" + "=" * 60)
if failures:
    print(f"  {len(failures)} FAILURE(S): {', '.join(failures)}")
    print("=" * 60 + "\n")
    raise SystemExit(1)
print("  All checks passed.")
print("=" * 60 + "\n")
