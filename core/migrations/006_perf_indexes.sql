-- Per-user queue and draft-list queries filtered by status/author and ordered
-- by time. Without these, a single user's queue page scans every pending post
-- in the DB (idx_scheduled_due is keyed on status first), and the draft list
-- builds a TEMP B-TREE to sort by updated_at.

CREATE INDEX IF NOT EXISTS idx_scheduled_user_status_time
    ON scheduled_posts(user_id, status, scheduled_at_utc);

CREATE INDEX IF NOT EXISTS idx_drafts_author_updated
    ON drafts(author_user_id, updated_at);
