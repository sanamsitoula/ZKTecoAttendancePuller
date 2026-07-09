"""Payroll & Nepal income-tax calculation engine (FY 2081/82).

Pure functions — no DB access — so the math is independently testable.

Tax model (individual, FY 2081/82 / 2024-25):

    Single (unmarried)                     Married (couple)
    ------------------------------------   ------------------------------------
    First   500,000        1%              First   600,000        1%
    Next    200,000       10%              Next    200,000       10%
    Next    300,000       20%              Next    300,000       20%
    Next  1,000,000       30%              Next    900,000       30%
    Above 2,000,000       36%              Above 2,000,000       36%

The 1% first tier is Social Security Tax.

Cumulative TDS: monthly tax is not a naive annual/12. Each period we project the
full-year taxable income, compute the annual tax, work out how much tax *should*
have been withheld through the current period, and deduct whatever hasn't been
withheld yet. So when income rises mid-year, the projected annual tax rises and
the shortfall is trued-up in the current and following periods automatically.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

# ── Tax slabs: (upper_bound_of_band_width, rate). None width = "remainder". ──
_SLABS = {
    "single": [
        (Decimal("500000"), Decimal("0.01")),
        (Decimal("200000"), Decimal("0.10")),
        (Decimal("300000"), Decimal("0.20")),
        (Decimal("1000000"), Decimal("0.30")),
        (None, Decimal("0.36")),
    ],
    "married": [
        (Decimal("600000"), Decimal("0.01")),
        (Decimal("200000"), Decimal("0.10")),
        (Decimal("300000"), Decimal("0.20")),
        (Decimal("900000"), Decimal("0.30")),
        (None, Decimal("0.36")),
    ],
}


def _q(x) -> Decimal:
    """Round to 2 decimal places, half-up."""
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x or 0))


def annual_tax(taxable_annual, marital: str = "single") -> Decimal:
    """Compute annual income tax for a given annual taxable amount (Rs)."""
    income = _D(taxable_annual)
    if income <= 0:
        return Decimal("0.00")
    slabs = _SLABS.get(marital, _SLABS["single"])
    tax = Decimal("0")
    remaining = income
    for width, rate in slabs:
        if remaining <= 0:
            break
        band = remaining if width is None else min(remaining, width)
        tax += band * rate
        remaining -= band
    return _q(tax)


def slab_breakdown(taxable_annual, marital: str = "single") -> list[dict]:
    """Return per-band detail for display on the payslip / tax preview."""
    income = _D(taxable_annual)
    slabs = _SLABS.get(marital, _SLABS["single"])
    rows, remaining, lower = [], income, Decimal("0")
    for width, rate in slabs:
        if remaining <= 0:
            break
        band = remaining if width is None else min(remaining, width)
        rows.append({
            "from": _q(lower),
            "to": _q(lower + band),
            "rate": float(rate),
            "rate_pct": f"{rate * 100:.0f}%",
            "amount": _q(band),
            "tax": _q(band * rate),
        })
        lower += band
        remaining -= band
    return rows


def fiscal_period_index(bs_month: int) -> int:
    """Map a BS month (1=Baisakh..12=Chaitra) to its Nepali fiscal-year index.

    The Nepali fiscal year starts in Shrawan (BS month 4), so Shrawan=1 .. Ashadh=12.
    """
    return ((int(bs_month) - 4) % 12) + 1


def monthly_tds(
    period_index: int,
    taxable_ytd_incl_current,
    this_month_taxable,
    tax_paid_before_current,
    marital: str = "single",
) -> dict:
    """Cumulative monthly TDS with automatic mid-year true-up.

    period_index            1..12 fiscal position of the current month.
    taxable_ytd_incl_current sum of taxable income from FY start through this month.
    this_month_taxable      current month's taxable income (used to project forward).
    tax_paid_before_current tax already withheld in earlier months of this FY.
    """
    pi = max(1, min(12, int(period_index)))
    ytd = _D(taxable_ytd_incl_current)
    cur = _D(this_month_taxable)
    paid = _D(tax_paid_before_current)

    # Project remaining months at the current month's rate.
    projected_annual = ytd + cur * (12 - pi)
    ann_tax = annual_tax(projected_annual, marital)

    # How much tax should have been withheld through the current period.
    tax_due_through_now = _q(ann_tax * Decimal(pi) / Decimal(12))
    tds = tax_due_through_now - paid
    if tds < 0:
        tds = Decimal("0.00")  # never refund via TDS; carries into next month's math
    return {
        "projected_annual_income": _q(projected_annual),
        "projected_annual_tax": ann_tax,
        "tax_due_through_now": tax_due_through_now,
        "tax_paid_before": _q(paid),
        "tds_this_month": _q(tds),
    }


def hourly_rate(basic_salary, working_days: int, daily_hours) -> Decimal:
    """Derive an hourly rate from monthly basic salary."""
    wd = max(1, int(working_days or 1))
    dh = _D(daily_hours) if _D(daily_hours) > 0 else Decimal("8")
    return _q(_D(basic_salary) / (Decimal(wd) * dh))


def ot_amount(basic_salary, working_days, daily_hours, ot_hours, multiplier) -> Decimal:
    """Overtime pay = hourly_rate × ot_hours × multiplier."""
    rate = hourly_rate(basic_salary, working_days, daily_hours)
    mult = _D(multiplier) if _D(multiplier) > 0 else Decimal("1.5")
    return _q(rate * _D(ot_hours) * mult)


def compute_payslip(
    *,
    basic_salary,
    allowances=0,
    working_days=30,
    present_days=None,
    daily_hours=8,
    ot_hours=0,
    ot_multiplier="1.5",
    holiday_ot_hours=0,
    holiday_ot_multiplier=None,
    other_earnings=0,
    other_deductions=0,
    marital="single",
    period_index=1,
    taxable_ytd_before=0,
    tax_paid_before=0,
) -> dict:
    """Compute a full monthly payslip breakdown.

    Salary is prorated by present_days/working_days. If present_days is None
    (no attendance data), the employee is treated as fully present.

    Overtime is split into two buckets:
      * ot_hours          — regular working-day OT, paid at ot_multiplier.
      * holiday_ot_hours  — hours worked on a weekly-off / holiday, paid at
                            holiday_ot_multiplier (e.g. 1.5×) when the employee
                            is eligible; falls back to ot_multiplier otherwise.
    """
    basic = _D(basic_salary)
    allow = _D(allowances)
    wd = max(1, int(working_days or 1))
    pd = wd if present_days is None else max(0, min(wd, int(present_days)))

    # Proration by attendance.
    prorate = Decimal(pd) / Decimal(wd)
    earned_basic = _q(basic * prorate)
    earned_allow = _q(allow * prorate)

    reg_ot_pay = ot_amount(basic, wd, daily_hours, ot_hours, ot_multiplier)
    hol_mult = holiday_ot_multiplier if holiday_ot_multiplier is not None else ot_multiplier
    hol_ot_pay = ot_amount(basic, wd, daily_hours, holiday_ot_hours, hol_mult)
    ot_pay = _q(reg_ot_pay + hol_ot_pay)
    other_earn = _D(other_earnings)

    gross = _q(earned_basic + earned_allow + ot_pay + other_earn)

    # Taxable income this month (gross; OT and allowances are taxable in Nepal).
    this_month_taxable = gross
    taxable_ytd_incl = _D(taxable_ytd_before) + this_month_taxable

    tax = monthly_tds(
        period_index=period_index,
        taxable_ytd_incl_current=taxable_ytd_incl,
        this_month_taxable=this_month_taxable,
        tax_paid_before_current=tax_paid_before,
        marital=marital,
    )
    tds = tax["tds_this_month"]
    other_ded = _D(other_deductions)
    total_deductions = _q(tds + other_ded)
    net_pay = _q(gross - total_deductions)

    return {
        "working_days": wd,
        "present_days": pd,
        "prorate": float(prorate),
        "earned_basic": earned_basic,
        "earned_allowance": earned_allow,
        "hourly_rate": hourly_rate(basic, wd, daily_hours),
        "ot_hours": float(_D(ot_hours)),
        "ot_multiplier": float(_D(ot_multiplier) or Decimal("1.5")),
        "holiday_ot_hours": float(_D(holiday_ot_hours)),
        "holiday_ot_multiplier": float(_D(hol_mult) or Decimal("1.5")),
        "regular_ot_pay": reg_ot_pay,
        "holiday_ot_pay": hol_ot_pay,
        "ot_pay": ot_pay,
        "other_earnings": _q(other_earn),
        "gross": gross,
        "taxable_this_month": this_month_taxable,
        "taxable_ytd": _q(taxable_ytd_incl),
        "tax": tds,
        "tax_detail": tax,
        "other_deductions": _q(other_ded),
        "total_deductions": total_deductions,
        "net_pay": net_pay,
    }
