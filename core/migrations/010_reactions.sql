ALTER TABLE scheduled_posts ADD COLUMN reaction_emojis_json TEXT NULL;

-- The reaction_presets table is created in the reconcile step
-- (core/state/base.py::_reconcile_reaction_presets), NOT here. A new table
-- defined in a late migration would be picked up by the baseline detector
-- (core/migrate.py scans the migration DDL) and make a legacy pre-migration DB
-- -- which cannot have this brand-new table -- fail to baseline, forcing a full
-- migration re-run. Defining it idempotently in reconcile covers fresh,
-- upgraded and baselined DBs alike, the same split already used for the
-- request_id index.
