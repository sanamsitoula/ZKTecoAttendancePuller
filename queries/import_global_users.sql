-- ─────────────────────────────────────────────────────────────────────────────
-- import_global_users.sql
--
-- Bulk-create Global Users from unlinked Device Employees.
--
-- What this does:
--   1. Finds every employees row where global_user_id IS NULL
--      AND no global_user already has that user_id (Att. ID).
--   2. De-duplicates across devices (same person enrolled on 3 devices
--      → picks the longest name as the canonical one).
--   3. INSERTs one global_users row per unique user_id.
--   4. UPDATEs ALL employees rows with that user_id to point at the new
--      global_users.id (covers all devices at once).
--
-- Run order:
--   Step 0 — preview (read-only, safe to run anytime)
--   Step 1 — INSERT global_users
--   Step 2 — UPDATE employees link
--   Step 3 — verify results
--
-- Run in psql:
--   psql -U postgres -d zkteco -f queries/import_global_users.sql
--
-- Or paste each block individually in pgAdmin / DBeaver.
-- ─────────────────────────────────────────────────────────────────────────────


-- ─── STEP 0 — PREVIEW (safe, no changes) ─────────────────────────────────────
-- Shows exactly what will be imported: unique user_ids, chosen name,
-- how many device-rows will be linked, and whether that Att. ID already
-- exists in global_users (would be skipped).

SELECT
    e.user_id                                           AS att_id,
    -- longest name across all devices for this user_id (most complete)
    (
        SELECT name
        FROM   employees
        WHERE  user_id = e.user_id
          AND  name IS NOT NULL AND name <> ''
        ORDER  BY length(name) DESC, id
        LIMIT  1
    )                                                   AS chosen_name,
    count(*)                                            AS device_rows,
    string_agg(DISTINCT d.name, ', ' ORDER BY d.name)  AS devices,
    CASE WHEN gu.id IS NOT NULL THEN 'SKIP (exists)' ELSE 'WILL IMPORT' END
                                                        AS action
FROM       employees   e
LEFT JOIN  devices     d  ON d.id  = e.device_id
LEFT JOIN  global_users gu ON gu.global_user_id = e.user_id
WHERE      e.global_user_id IS NULL
GROUP BY   e.user_id, gu.id
ORDER BY
    CASE WHEN gu.id IS NOT NULL THEN 1 ELSE 0 END,  -- WILLs first
    CASE WHEN e.user_id ~ '^[0-9]+$'
         THEN e.user_id::integer ELSE NULL END NULLS LAST,
    e.user_id;


-- ─── STEP 1 — INSERT global_users ────────────────────────────────────────────
-- Creates one global_users row per unique user_id that has no existing match.
-- Name = longest non-blank name found across all devices for that user_id.
-- ON CONFLICT DO NOTHING  →  safe to run multiple times.

INSERT INTO global_users (global_user_id, name)
SELECT DISTINCT ON (e.user_id)
    e.user_id   AS global_user_id,
    COALESCE(
        (
            SELECT name
            FROM   employees
            WHERE  user_id = e.user_id
              AND  name IS NOT NULL AND name <> ''
            ORDER  BY length(name) DESC, id
            LIMIT  1
        ),
        e.user_id   -- fall back to the Att. ID if all names are blank
    )           AS name
FROM  employees e
WHERE e.global_user_id IS NULL
  AND NOT EXISTS (
      SELECT 1
      FROM   global_users gu
      WHERE  gu.global_user_id = e.user_id
  )
ORDER BY e.user_id, e.id
ON CONFLICT (global_user_id) DO NOTHING;


-- ─── STEP 2 — LINK employees → global_users ──────────────────────────────────
-- Sets global_user_id on every unlinked employees row whose user_id now
-- matches a global_users record (covers all devices in one pass).

UPDATE employees e
SET    global_user_id = gu.id
FROM   global_users gu
WHERE  e.user_id        = gu.global_user_id
  AND  e.global_user_id IS NULL;


-- ─── STEP 3 — VERIFY ─────────────────────────────────────────────────────────

-- 3a) Overall counts
SELECT
    count(*)                                          AS total_employees,
    count(global_user_id)                             AS linked,
    count(*) FILTER (WHERE global_user_id IS NULL)    AS still_unlinked
FROM employees;

-- 3b) Total global users now
SELECT count(*) AS total_global_users FROM global_users;

-- 3c) Global users still missing a name (need manual edit)
SELECT id, global_user_id, name
FROM   global_users
WHERE  name IS NULL OR name = ''
ORDER  BY global_user_id::integer NULLS LAST
LIMIT  20;

-- 3d) Any employees still unlinked (user_id has no match in global_users)
SELECT e.user_id, e.name, d.name AS device, count(*) AS rows
FROM   employees  e
JOIN   devices    d ON d.id = e.device_id
WHERE  e.global_user_id IS NULL
GROUP  BY e.user_id, e.name, d.name
ORDER  BY e.user_id
LIMIT  20;
