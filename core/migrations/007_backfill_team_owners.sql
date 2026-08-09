-- One-time repair for teams created before create_team inserted the owner as a
-- team_members row. Previously this ran on every startup (full-table UPDATE +
-- INSERT-SELECT); it belongs in a migration that runs once. Timestamps use the
-- migration time, which only matters for legacy rows actually being repaired.
UPDATE team_members
SET role='owner', updated_at=CAST(strftime('%s', 'now') AS INTEGER)
WHERE (team_id, user_id) IN (
    SELECT id, owner_user_id FROM teams
)
  AND role <> 'owner';

INSERT INTO team_members(team_id, user_id, role, created_at, updated_at)
SELECT t.id, t.owner_user_id, 'owner',
       CAST(strftime('%s', 'now') AS INTEGER),
       CAST(strftime('%s', 'now') AS INTEGER)
FROM teams t
WHERE NOT EXISTS (
    SELECT 1
    FROM team_members tm
    WHERE tm.team_id = t.id
      AND tm.user_id = t.owner_user_id
);
