"""
Verify attendance data in attendance_logs for any BS month.
Both Hajiri Report and Monthly Report read from attendance_logs directly --
no settlement needed. This script confirms data is present.

Usage:
  python report_month.py 2083 2     <- Jestha 2083
  python report_month.py 2083 3     <- Ashadh 2083
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
from nepali_utils import bs_month_info, NEPALI_MONTHS

def main():
    if len(sys.argv) < 3:
        print("Usage: python report_month.py <bs_year> <bs_month>")
        print("  e.g. python report_month.py 2083 2")
        sys.exit(1)

    bs_year  = int(sys.argv[1])
    bs_month = int(sys.argv[2])

    mi = bs_month_info(bs_year, bs_month)
    if mi is None:
        print(f"ERROR: invalid BS year/month {bs_year}/{bs_month}")
        sys.exit(1)

    month_name = NEPALI_MONTHS[bs_month] if 1 <= bs_month <= 12 else str(bs_month)
    print(f"\n{'='*55}")
    print(f"  {month_name} {bs_year}  ({mi['days']} days)")
    print(f"  AD range : {mi['first_ad']}  to  {mi['last_ad']}")
    print(f"{'='*55}")

    conn = db.get_connection()
    db.init_schema(conn)
    conn.commit()

    sql_daily = """
        SELECT
            (al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date  AS work_date,
            COUNT(DISTINCT e.global_user_id)                    AS users_present,
            COUNT(*)                                            AS total_punches
        FROM attendance_logs al
        JOIN employees e
          ON al.device_id = e.device_id AND al.user_id = e.user_id
        WHERE e.global_user_id IS NOT NULL
          AND (al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date BETWEEN %s AND %s
        GROUP BY work_date
        ORDER BY work_date
    """
    with conn.cursor() as cur:
        cur.execute(sql_daily, (mi['first_ad'], mi['last_ad']))
        rows = cur.fetchall()

    if not rows:
        print("\n  No data found in attendance_logs for this period.")
        print("  Pull from devices via the Dashboard first.\n")
        conn.close()
        return

    print(f"\n  {'Date':<14} {'Employees':>10} {'Punches':>10}")
    print(f"  {'-'*36}")
    for r in rows:
        print(f"  {str(r[0]):<14} {r[1]:>10} {r[2]:>10}")
    print(f"  {'-'*36}")
    print(f"  Days with data  : {len(rows)} / {mi['days']}")
    print(f"  Total emp-days  : {sum(r[1] for r in rows)}")
    print(f"  Total punches   : {sum(r[2] for r in rows)}")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(DISTINCT e.global_user_id)
            FROM attendance_logs al
            JOIN employees e
              ON al.device_id = e.device_id AND al.user_id = e.user_id
            WHERE e.global_user_id IS NOT NULL
              AND (al.timestamp AT TIME ZONE 'Asia/Kathmandu')::date BETWEEN %s AND %s
        """, (mi['first_ad'], mi['last_ad']))
        user_count = cur.fetchone()[0]

    print(f"  Unique employees: {user_count}")
    print(f"\n  Open in browser:")
    print(f"    Monthly : /reports/monthly?bs_year={bs_year}&bs_month={bs_month}")
    print(f"    Hajiri  : /reports/hajiri?bs_year={bs_year}&bs_month={bs_month}\n")

    conn.close()

if __name__ == "__main__":
    main()
