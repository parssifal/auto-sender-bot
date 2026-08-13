ALTER TABLE scheduled_posts ADD COLUMN request_id TEXT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduled_user_request_id
    ON scheduled_posts(user_id, request_id)
    WHERE request_id IS NOT NULL;
