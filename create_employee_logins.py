"""
One-time (or occasional) bulk creator for employee self-service web logins.

For every Global User that doesn't already have a linked web_users login,
creates one with:
  - username = their attendance device ID (global_users.global_user_id)
  - password = same as the username (must be changed on first login)
  - role     = 'employee' (self-service: own attendance + leaves only)

Safe to re-run — employees who already have a login are skipped.

Usage:
  python create_employee_logins.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db


def main():
    conn = db.get_connection()
    db.init_schema(conn)
    conn.commit()

    gu_ids = db.get_global_user_ids_without_web_login(conn)
    if not gu_ids:
        print("Every Global User already has a web login. Nothing to do.")
        conn.close()
        return

    print(f"Creating employee logins for {len(gu_ids)} employee(s)...\n")
    created, skipped = 0, 0
    for gu_id in gu_ids:
        result = db.create_employee_login(conn, gu_id, created_by=0)
        conn.commit()
        if result.get("created"):
            created += 1
            print(f"  + {result['username']}")
        else:
            skipped += 1
            print(f"  - skipped (gu id {gu_id}): {result.get('reason')}")

    conn.close()
    print(f"\nDone. Created {created}, skipped {skipped}.")
    print("Default password for each new login is the same as its username —")
    print("employees are prompted to change it on first login.")


if __name__ == "__main__":
    main()
