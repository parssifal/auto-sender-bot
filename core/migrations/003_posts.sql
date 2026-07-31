CREATE TABLE IF NOT EXISTS scheduled_posts (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    scheduled_at_utc INTEGER NOT NULL,
    status TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NULL,
    entities_json TEXT NULL,
    caption TEXT NULL,
    caption_entities_json TEXT NULL,
    caption_above INTEGER NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at_utc INTEGER NULL,
    created_at INTEGER NOT NULL,
    sent_at INTEGER NULL,
    last_error TEXT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scheduled_due
    ON scheduled_posts(status, scheduled_at_utc, next_retry_at_utc);

CREATE TABLE IF NOT EXISTS scheduled_post_media (
    post_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    type TEXT NOT NULL,
    file_id TEXT NOT NULL,
    PRIMARY KEY (post_id, idx),
    FOREIGN KEY (post_id) REFERENCES scheduled_posts(id) ON DELETE CASCADE
);
