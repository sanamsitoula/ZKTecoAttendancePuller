"""
Shared attendance computation engine.

Extracted from web/app.py (payroll_plan.md Section 8 / Phase 7) so the
attendance reports (/reports/monthly/view, /reports/monthly/print-all,
/reports/hajiri) and payroll generation both compute present/absent/OT
figures from the exact same code path — no second, silently-divergent
source of truth (previously payroll read from the separate `attendance_daily`
pre-aggregation table instead of this live pipeline).

Behavior is unchanged from the code this was extracted from — this is a
relocation, not a rewrite. web/app.py's report routes now import from here
instead of defining these functions inline.
"""
from __future__ import annotations

from datetime import date, timedelta

from nepali_utils import ad_to_bs_tuple

NEPAL_DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']


def fmt_min(minutes: int | None) -> str:
    if minutes is None or minutes <= 0:
        return ''
    h, m = divmod(int(minutes), 60)
    return f"{h:02d}:{m:02d}"


def time_to_min(t) -> int | None:
    """Convert a datetime.time or HH:MM string to minutes since midnight."""
    if t is None:
        return None
    try:
        if hasattr(t, 'hour'):
            return t.hour * 60 + t.minute
        parts = str(t).split(':')
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return None


def compute_monthly_report(daily_rows: list, from_ad: str, to_ad: str,
                            default_si_min: int = 600, default_so_min: int = 1020,
                            shift_calendar: dict = None,
                            holiday_map: dict = None,
                            leave_map: set = None) -> list:
    """Build per-day dicts matching the 16-column ZKBioTime periodic attendance format.

    Columns: Work Date | Planned In | Planned Out | Work Time |
             Time In | Time Out | Break In | Break Out | Time |
             Actual | OT | LateIn | EarlyOut | EarlyIn | LateOut | Remark
    """
    punch_map = {r['work_date']: r for r in daily_rows}
    start = date.fromisoformat(from_ad)
    end = date.fromisoformat(to_ad)

    def _pstr(pd) -> str:
        if not pd:
            return ''
        t = pd.get('time')
        return t.strftime('%H:%M') if hasattr(t, 'strftime') else str(t)[:5]

    days = []
    d = start
    while d <= end:
        bs_t = ad_to_bs_tuple(d)
        bs_str = f"{bs_t[2]:02d}/{bs_t[1]:02d}/{bs_t[0]}" if bs_t else ''
        nepal_dow = d.isoweekday() % 7
        day_name = NEPAL_DAYS[nepal_dow]
        is_weekend = (nepal_dow == 6)

        # Per-day shift lookup (employee-specific or department-level via shift_calendar)
        shift_info = (shift_calendar or {}).get(d)
        si_min = shift_info['start_min'] if shift_info else default_si_min
        so_min = shift_info['end_min'] if shift_info else default_so_min
        shift_name = shift_info['name'] if shift_info else ''
        planned_work = so_min - si_min

        holiday_entry = (holiday_map or {}).get(d.isoformat())
        is_holiday = bool(holiday_entry)
        holiday_type = (holiday_entry or {}).get('holiday_type', 'public') if is_holiday else ''
        is_off_day = is_weekend or is_holiday

        row = punch_map.get(d)

        pts = []
        if row:
            if row.get('all_punches_with_device'):
                pts = [p for p in row['all_punches_with_device'] if p]
            elif row.get('all_punch_times'):
                pts = [{'time': p, 'device_name': ''} for p in row['all_punch_times'] if p]

        first_punch = row['first_punch'] if row else None
        last_punch = row['last_punch'] if row else None

        # 1 punch: Time In only
        # 2 punches: Time In + Break Out
        # 3 punches: Time In + Time Out + Break Out
        # 4+:        Time In + Time Out + Break In + Break Out
        time_in = _pstr(pts[0]) if len(pts) >= 1 else ''
        time_out = _pstr(pts[1]) if len(pts) >= 3 else ''
        break_in = _pstr(pts[2]) if len(pts) >= 4 else ''
        break_out = _pstr(pts[-1]) if len(pts) >= 2 else ''

        ci_min = time_to_min(first_punch)
        co_min = time_to_min(last_punch) if (last_punch and first_punch and last_punch != first_punch) else None

        work_min = (co_min - ci_min) if (ci_min is not None and co_min is not None and co_min > ci_min) else None
        if work_min is not None:
            time_col = fmt_min(work_min)
        elif len(pts) == 1:
            time_col = _pstr(pts[0])
        else:
            time_col = ''

        late_in = early_out = early_in = late_out = ot = ''
        if not is_off_day and ci_min is not None:
            if ci_min > si_min:
                late_in = fmt_min(ci_min - si_min)
            elif ci_min < si_min:
                early_in = fmt_min(si_min - ci_min)
        if not is_off_day and co_min is not None:
            if co_min < so_min:
                early_out = fmt_min(so_min - co_min)
            elif co_min > so_min:
                late_out = fmt_min(co_min - so_min)
        if not is_off_day and work_min and work_min > planned_work:
            ot = fmt_min(work_min - planned_work)

        is_on_leave = (not row) and (d.isoformat() in (leave_map or set()))

        if is_weekend:
            remark = 'Weekend'
        elif is_holiday:
            remark = 'Festival' if holiday_type == 'festival' else 'Holiday'
        elif row:
            remark = 'Present'
        elif is_on_leave:
            remark = 'Leave'
        else:
            remark = 'Absent'

        days.append({
            'bs_date': bs_str,
            'ad_date': d.isoformat(),
            'day_name': day_name,
            'shift_name': shift_name,
            'planned_in': fmt_min(si_min) if not is_off_day else '00:00',
            'planned_out': fmt_min(so_min) if not is_off_day else '00:00',
            'planned_work': fmt_min(planned_work) if not is_off_day else '',
            'planned_min': planned_work if not is_off_day else 0,
            'time_in': time_in,
            'time_out': time_out,
            'break_in': break_in,
            'break_out': break_out,
            'time_col': time_col,
            'actual': time_col,
            'ot': ot,
            'late_in': late_in,
            'early_out': early_out,
            'early_in': early_in,
            'late_out': late_out,
            'remark': remark,
            'work_min': work_min or 0,
        })
        d += timedelta(days=1)

    return days


def monthly_totals(days: list, planned_work: int = 0) -> dict:
    tot_actual = tot_ot = tot_late_in = tot_early_out = tot_early_in = tot_late_out = 0
    tot_planned = 0

    counts = {'Present': 0, 'Absent': 0, 'Weekend': 0, 'Holiday': 0, 'Festival': 0, 'Leave': 0, 'Misc': 0}
    for d in days:
        tot_actual += d.get('work_min', 0)
        # Sum per-day planned for all workdays (not off-days or leaves)
        if d['remark'] not in ('Weekend', 'Holiday', 'Festival', 'Leave'):
            tot_planned += d.get('planned_min', 0)

        def _parse(s):
            if not s:
                return 0
            try:
                p = str(s).split(':')
                return int(p[0]) * 60 + int(p[1])
            except Exception:
                return 0
        tot_ot += _parse(d['ot'])
        tot_late_in += _parse(d['late_in'])
        tot_early_out += _parse(d['early_out'])
        tot_early_in += _parse(d['early_in'])
        tot_late_out += _parse(d['late_out'])
        r = d['remark']
        if r in counts:
            counts[r] += 1

    working_days = len(days) - counts['Weekend'] - counts['Holiday'] - counts['Festival']
    return {
        'planned': fmt_min(tot_planned),
        'actual': fmt_min(tot_actual),
        'ot': fmt_min(tot_ot),
        'late_in': fmt_min(tot_late_in),
        'early_out': fmt_min(tot_early_out),
        'early_in': fmt_min(tot_early_in),
        'late_out': fmt_min(tot_late_out),
        'counts': counts,
        'working_days': working_days,
        'total_days': len(days),
    }
