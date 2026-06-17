"""
Run attendance settlement for any BS month.
Usage:
  python settle_month.py 2083 2        <- Jestha 2083
  python settle_month.py 2083 3        <- Ashadh 2083
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
from nepali_utils import bs_month_info, NEPALI_MONTHS

def main():
    if len(sys.argv) < 3:
        print("Usage: python settle_month.py <bs_year> <bs_month>")
        print("  e.g. python settle_month.py 2083 2")
        sys.exit(1)

    bs_year  = int(sys.argv[1])
    bs_month = int(sys.argv[2])

    mi = bs_month_info(bs_year, bs_month)
    if mi is None:
        print(f"ERROR: invalid BS year/month: {bs_year}/{bs_month}")
        sys.exit(1)

    month_name = NEPALI_MONTHS[bs_month] if 1 <= bs_month <= 12 else str(bs_month)
    print(f"\n{'='*55}")
    print(f"  Settling: {month_name} {bs_year}  ({mi['days']} days)")
    print(f"  AD range: {mi['first_ad']}  to  {mi['last_ad']}")
    print(f"{'='*55}\n")

    conn = db.get_connection()
    db.init_schema(conn)
    conn.commit()

    print("Running settlement... (this may take a moment)")
    result = db.settle_all_attendance_daily(conn, mi['first_ad'], mi['last_ad'])
    conn.commit()
    conn.close()

    print(f"\nDone!")
    print(f"  Users processed : {result['users']}")
    print(f"  Day rows settled: {result['settled_days']}")
    print(f"\nOpen Reports -> Hajiri Report and select {month_name} {bs_year}\n")

if __name__ == "__main__":
    main()
