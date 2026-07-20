"""Payroll & Nepal income-tax calculation engine.

Pure functions — no DB access — so the math is independently testable.
Tax slabs are NOT hardcoded here: Nepal's slabs change most budgets (e.g. the
FY2083/84 proposal removes the single/married split and changes every rate),
so every function that needs a slab table takes one as a `bands` argument —
resolved by the caller from `payroll_tax_slab_sets`/`payroll_tax_slab_bands`
via `db.get_tax_slab_bands()` for the run's actual fiscal year. See
payroll_plan.md Section 6.1.

`bands` shape: a list of {'width': Decimal|None, 'rate': Decimal} dicts, in
order, where `width` is the band's Rs width and `rate` is a *percentage*
(e.g. 10 for 10%, not 0.10) — `rate=None`'s width entry marks the final,
open-ended "remainder" band.

Cumulative TDS: monthly tax is not a naive annual/12. Each period we project the
full-year taxable income, compute the annual tax, work out how much tax *should*
have been withheld through the current period, and deduct whatever hasn't been
withheld yet. So when income rises mid-year, the projected annual tax rises and
the shortfall is trued-up in the current and following periods automatically.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def _q(x) -> Decimal:
    """Round to 2 decimal places, half-up."""
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x or 0))


def _normalize_bands(bands) -> list[tuple]:
    """Accept bands as dicts ({'width','rate'}, from db.get_tax_slab_bands)
    or plain (width, rate) tuples; return a list of (width: Decimal|None, rate: Decimal) tuples.
    `rate` is normalized from a percentage (e.g. 10) to a fraction (0.10)."""
    out = []
    for b in bands:
        if isinstance(b, dict):
            width, rate = b.get("width"), b.get("rate")
        else:
            width, rate = b
        w = None if width is None else _D(width)
        r = _D(rate) / Decimal("100")
        out.append((w, r))
    return out


def annual_tax(taxable_annual, bands) -> Decimal:
    """Compute annual income tax for a given annual taxable amount (Rs)."""
    income = _D(taxable_annual)
    if income <= 0:
        return Decimal("0.00")
    slabs = _normalize_bands(bands)
    tax = Decimal("0")
    remaining = income
    for width, rate in slabs:
        if remaining <= 0:
            break
        band = remaining if width is None else min(remaining, width)
        tax += band * rate
        remaining -= band
    return _q(tax)


def slab_breakdown(taxable_annual, bands) -> list[dict]:
    """Return per-band detail for display on the payslip / tax preview."""
    income = _D(taxable_annual)
    slabs = _normalize_bands(bands)
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


def fiscal_period_index(bs_month: int, fiscal_start_month: int = 4) -> int:
    """Map a BS month (1=Baisakh..12=Chaitra) to its fiscal-year index (1..12).

    `fiscal_start_month` is the BS month the fiscal year begins in — resolve
    it from the run's `fiscal_years.start_bs` (Section 5.1), never assume it;
    it defaults to 4 (Shrawan, Nepal's standard fiscal year start) only for
    callers that haven't been updated to pass it explicitly.
    """
    return ((int(bs_month) - int(fiscal_start_month)) % 12) + 1


def monthly_tds(
    period_index: int,
    taxable_ytd_incl_current,
    this_month_taxable,
    tax_paid_before_current,
    bands,
) -> dict:
    """Cumulative monthly TDS with automatic mid-year true-up.

    period_index            1..12 fiscal position of the current month.
    taxable_ytd_incl_current sum of taxable income from FY start through this month.
    this_month_taxable      current month's taxable income (used to project forward).
    tax_paid_before_current tax already withheld in earlier months of this FY.
    bands                   this run's resolved tax slab bands (db.get_tax_slab_bands()).
    """
    pi = max(1, min(12, int(period_index)))
    ytd = _D(taxable_ytd_incl_current)
    cur = _D(this_month_taxable)
    paid = _D(tax_paid_before_current)

    # Project remaining months at the current month's rate.
    projected_annual = ytd + cur * (12 - pi)
    ann_tax = annual_tax(projected_annual, bands)

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
    pretax_deductions=None,
    tax_bands=None,
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

    `tax_bands` is this run's resolved fiscal-year tax slab bands (from
    `db.get_tax_slab_bands()`) — required; there is no built-in default slab
    table, since slabs are fiscal-year data, not code (payroll_plan.md 6.1).

    `pretax_deductions` is this month's resolved statutory deductions (from
    `db.resolve_employee_deductions_for_month()` — PF/CIT/Insurance etc.) as
    a list of {'code', 'name', 'amount', ...} dicts, or a plain number. These
    reduce TAXABLE income before the slab calculation, per payroll_plan.md
    Section 6.3 — this is the fix for the previous behavior where taxable
    income was simply `gross`, ignoring deductions entirely. Non-pre-tax,
    ad-hoc deductions still go through `other_deductions` (subtracted from
    net pay only, unchanged from before).
    """
    if not tax_bands:
        raise ValueError("compute_payslip() requires tax_bands (resolved from "
                          "db.get_tax_slab_bands() for the run's fiscal year) — "
                          "tax slabs are no longer hardcoded.")
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

    # Pre-tax deductions (PF/CIT/Insurance/...) reduce taxable income before
    # the slab calculation — the Section 6.3 fix. Accepts either a resolved
    # detail list (preferred — feeds the payslip formula breakdown) or a
    # plain pre-summed number.
    if pretax_deductions is None:
        pretax_list = []
        pretax_total = Decimal("0")
    elif isinstance(pretax_deductions, (list, tuple)):
        pretax_list = [{"code": d.get("code"), "name": d.get("name"), "amount": _q(d.get("amount", 0))}
                       for d in pretax_deductions]
        pretax_total = sum((item["amount"] for item in pretax_list), Decimal("0"))
    else:
        pretax_list = []
        pretax_total = _D(pretax_deductions)
    pretax_total = _q(pretax_total)

    # Taxable income this month = gross minus pre-tax deductions, floored at
    # zero (a deduction total can never make taxable income negative).
    this_month_taxable = gross - pretax_total
    if this_month_taxable < 0:
        this_month_taxable = Decimal("0.00")
    taxable_ytd_incl = _D(taxable_ytd_before) + this_month_taxable

    tax = monthly_tds(
        period_index=period_index,
        taxable_ytd_incl_current=taxable_ytd_incl,
        this_month_taxable=this_month_taxable,
        tax_paid_before_current=tax_paid_before,
        bands=tax_bands,
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
        "pretax_deductions": pretax_list,
        "pretax_deductions_total": pretax_total,
        "taxable_this_month": this_month_taxable,
        "taxable_ytd": _q(taxable_ytd_incl),
        "tax": tds,
        "tax_detail": tax,
        "other_deductions": _q(other_ded),
        "total_deductions": total_deductions,
        "net_pay": net_pay,
    }
