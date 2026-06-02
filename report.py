"""
Daily attendance report image generator.

Generates a PNG image per day showing:
  - Employee name, first check-in, last check-out, duration, devices used
  - Summary bar at the top: total employees, total punches
  - Saved to REPORTS_DIR/<date>.png
"""
import logging
import os
from datetime import datetime, timezone, timedelta

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — safe for daemon/cron use
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import matplotlib.ticker as ticker

from config import REPORTS_DIR

logger = logging.getLogger(__name__)


def _duration_str(first_in, last_out) -> str:
    if first_in is None or last_out is None:
        return "—"
    delta = last_out - first_in
    total_minutes = int(delta.total_seconds() // 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m:02d}m"


def _fmt_time(dt) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%H:%M:%S")


def generate_daily_report(date_str: str, summary_rows: list, output_dir: str = REPORTS_DIR) -> str:
    """
    Generate a daily attendance PNG report.

    Parameters
    ----------
    date_str    : "YYYY-MM-DD"
    summary_rows: list of dicts from db.get_daily_summary()
    output_dir  : directory to save the PNG

    Returns
    -------
    Absolute path of the saved PNG file.
    """
    os.makedirs(output_dir, exist_ok=True)

    total_employees = len(summary_rows)
    total_punches   = sum(r["total_punches"] for r in summary_rows)

    # ── layout ───────────────────────────────────────────────────────────────
    row_height = 0.35          # inches per data row
    header_h   = 2.5           # inches for title + summary block
    fig_h      = max(6, header_h + total_employees * row_height + 1)
    fig_w      = 16

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor="#1a1a2e")
    gs  = GridSpec(2, 1, figure=fig, height_ratios=[header_h, fig_h - header_h],
                   hspace=0.02)

    # ── header panel ─────────────────────────────────────────────────────────
    ax_hdr = fig.add_subplot(gs[0])
    ax_hdr.set_facecolor("#16213e")
    ax_hdr.axis("off")

    ax_hdr.text(0.5, 0.78, "ZKTeco Attendance Report",
                transform=ax_hdr.transAxes, fontsize=22, fontweight="bold",
                color="#e94560", ha="center", va="center")
    ax_hdr.text(0.5, 0.52, date_str,
                transform=ax_hdr.transAxes, fontsize=16,
                color="#a8dadc", ha="center", va="center")

    # summary chips
    chip_texts = [
        f"Employees Present: {total_employees}",
        f"Total Punches: {total_punches}",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    for i, txt in enumerate(chip_texts):
        x = 0.18 + i * 0.32
        ax_hdr.text(x, 0.22, txt,
                    transform=ax_hdr.transAxes, fontsize=11,
                    color="#ffffff", ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.4", facecolor="#0f3460",
                              edgecolor="#e94560", linewidth=1.2))

    # ── table panel ──────────────────────────────────────────────────────────
    ax_tbl = fig.add_subplot(gs[1])
    ax_tbl.set_facecolor("#1a1a2e")
    ax_tbl.axis("off")

    col_labels = ["#", "Employee Name", "User ID", "First Check-In",
                  "Last Check-Out", "Duration", "Punches", "Device(s)"]
    col_widths = [0.03, 0.22, 0.07, 0.12, 0.12, 0.09, 0.07, 0.18]

    # column header
    y_top = 0.97
    x_cursor = 0.01
    for label, w in zip(col_labels, col_widths):
        ax_tbl.text(x_cursor + w / 2, y_top, label,
                    transform=ax_tbl.transAxes, fontsize=9.5, fontweight="bold",
                    color="#e94560", ha="center", va="top")
        x_cursor += w

    # separator line
    ax_tbl.plot([0.01, 0.99], [y_top - 0.03, y_top - 0.03],
                transform=ax_tbl.transAxes, color="#e94560", linewidth=1)

    if not summary_rows:
        ax_tbl.text(0.5, 0.5, "No attendance records for this date.",
                    transform=ax_tbl.transAxes, fontsize=14,
                    color="#a8dadc", ha="center", va="center")
    else:
        row_step = (y_top - 0.05) / max(total_employees, 1)

        for idx, row in enumerate(summary_rows):
            y = y_top - 0.06 - idx * row_step
            bg_color = "#0d2137" if idx % 2 == 0 else "#162032"

            # row background
            rect = mpatches.FancyBboxPatch(
                (0.005, y - row_step * 0.45),
                0.99, row_step * 0.88,
                boxstyle="round,pad=0.001",
                transform=ax_tbl.transAxes,
                facecolor=bg_color, edgecolor="none", zorder=0,
            )
            ax_tbl.add_patch(rect)

            cells = [
                str(idx + 1),
                row["name"] or "Unknown",
                str(row["user_id"]),
                _fmt_time(row["first_in"]),
                _fmt_time(row["last_out"]),
                _duration_str(row["first_in"], row["last_out"]),
                str(row["total_punches"]),
                row["devices"] or "—",
            ]

            x_cursor = 0.01
            for cell, w in zip(cells, col_widths):
                ax_tbl.text(x_cursor + w / 2, y,
                            cell, transform=ax_tbl.transAxes,
                            fontsize=8.5, color="#dde6ed",
                            ha="center", va="center")
                x_cursor += w

    # footer
    fig.text(0.5, 0.005,
             "ZKTeco Attendance Puller  •  Automated Report  •  beamlab.dev",
             ha="center", fontsize=8, color="#555577")

    # ── save ─────────────────────────────────────────────────────────────────
    out_path = os.path.join(output_dir, f"{date_str}.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)

    logger.info("Daily report saved: %s (%d employees)", out_path, total_employees)
    return os.path.abspath(out_path)


def generate_device_timeline(date_str: str, records: list, output_dir: str = REPORTS_DIR) -> str:
    """
    Generate a timeline bar chart showing check-in/out events per employee per device.

    Parameters
    ----------
    date_str : "YYYY-MM-DD"
    records  : list of dicts from db.get_attendance_for_date()
    """
    os.makedirs(output_dir, exist_ok=True)

    if not records:
        logger.info("No records for %s — skipping timeline.", date_str)
        return ""

    # Collect unique employees (by user_id)
    emp_order = []
    seen = set()
    for r in records:
        key = r["user_id"]
        if key not in seen:
            seen.add(key)
            emp_order.append((key, r["name"]))

    # Cap at 60 employees for readability
    emp_order = emp_order[:60]
    emp_index = {uid: i for i, (uid, _) in enumerate(emp_order)}

    device_colors = {}
    palette = ["#e94560", "#0f3460", "#53d8fb", "#f5a623", "#7ed321", "#bd10e0"]
    devices_seen = sorted({r["device_name"] for r in records})
    for i, d in enumerate(devices_seen):
        device_colors[d] = palette[i % len(palette)]

    fig_h = max(5, len(emp_order) * 0.28 + 2)
    fig, ax = plt.subplots(figsize=(16, fig_h), facecolor="#1a1a2e")
    ax.set_facecolor("#12122a")

    base_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    for r in records:
        uid = r["user_id"]
        if uid not in emp_index:
            continue
        y = emp_index[uid]
        ts = r["timestamp"]
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        x = (ts - base_dt).total_seconds() / 3600  # hours since midnight
        color = device_colors.get(r["device_name"], "#ffffff")
        marker = "^" if (r["punch"] in (0, 255)) else "v"
        ax.scatter(x, y, c=color, marker=marker, s=60, zorder=3, linewidths=0)

    ax.set_yticks(range(len(emp_order)))
    ax.set_yticklabels(
        [f"{name} ({uid})" for uid, name in emp_order],
        fontsize=7, color="#dde6ed"
    )
    ax.set_xlabel("Hour of Day (UTC)", color="#a8dadc", fontsize=10)
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 2))
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{int(v):02d}:00"))
    ax.tick_params(colors="#a8dadc")
    ax.grid(axis="x", color="#2a2a4a", linewidth=0.5)
    ax.set_title(f"Attendance Timeline — {date_str}", color="#e94560",
                 fontsize=14, fontweight="bold", pad=12)

    # legend
    legend_patches = [
        mpatches.Patch(color=c, label=d) for d, c in device_colors.items()
    ]
    legend_patches += [
        plt.scatter([], [], marker="^", c="#ffffff", label="Check-In"),
        plt.scatter([], [], marker="v", c="#ffffff", label="Check-Out"),
    ]
    ax.legend(handles=legend_patches, loc="upper right",
              facecolor="#0f3460", edgecolor="#e94560",
              labelcolor="#dde6ed", fontsize=8)

    fig.tight_layout()
    out_path = os.path.join(output_dir, f"{date_str}_timeline.png")
    fig.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("Timeline chart saved: %s", out_path)
    return os.path.abspath(out_path)
