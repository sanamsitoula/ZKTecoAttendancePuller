"""
One-time script: set global_users.emp_type based on global_users.id ranges.

Rule (as specified by the business):
  - global_id 1                -> CONTRACT
  - global_id 2 to 528         -> PERMANENT
  - global_id 529 and above    -> CONTRACT

Safe to re-run (idempotent) — it always sets emp_type according to the same
ranges, it doesn't skip already-set rows.

Usage:
  python set_emp_type_by_global_id.py          # apply changes
  python set_emp_type_by_global_id.py --dry-run  # preview only, no writes
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db


def main():
    dry_run = "--dry-run" in sys.argv

    conn = db.get_connection()
    db.init_schema(conn)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT id, global_user_id, name, emp_type FROM global_users ORDER BY id")
        rows = cur.fetchall()

    changes = []
    for row_id, gu_id, name, current_type in rows:
        if row_id == 1:
            new_type = "CONTRACT"
        elif 2 <= row_id <= 528:
            new_type = "PERMANENT"
        else:  # row_id >= 529
            new_type = "CONTRACT"

        if current_type != new_type:
            changes.append((row_id, gu_id, name, current_type, new_type))

    if not changes:
        print("No changes needed — emp_type already matches the target ranges for all rows.")
        conn.close()
        return

    print(f"{len(changes)} of {len(rows)} global_users row(s) will change:\n")
    for row_id, gu_id, name, old, new in changes:
        print(f"  id={row_id:<6} global_user_id={gu_id or '':<12} name={(name or ''):<30} {old} -> {new}")

    if dry_run:
        print("\nDry run — no changes written.")
        conn.close()
        return

    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE global_users SET emp_type = %s WHERE id = %s",
            [(new, row_id) for row_id, _, _, _, new in changes],
        )
    conn.commit()
    conn.close()
    print(f"\nDone. Updated emp_type for {len(changes)} global_users row(s).")


if __name__ == "__main__":
    main()
