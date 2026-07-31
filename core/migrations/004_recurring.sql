CREATE TABLE IF NOT EXISTS recurring_patterns (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    interval_type TEXT NOT NULL,
    weekdays_mask INTEGER NULL,
    time_of_day_minutes INTEGER NOT NULL,
    timezone TEXT NOT NULL,
    start_at_utc INTEGER NOT NULL,
    end_at_utc INTEGER NULL,
    max_occurrences INTEGER NULL,
    current_count INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    CHECK (time_of_day_minutes BETWEEN 0 AND 1439),
    CHECK (weekdays_mask IS NULL OR weekdays_mask BETWEEN 1 AND 127),
    CHECK (max_occurrences IS NULL OR max_occurrences > 0),
    CHECK (current_count >= 0),
    CHECK (is_active IN (0, 1)),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recurring_patterns_user_active
    ON recurring_patterns(user_id, is_active, created_at);

CREATE INDEX IF NOT EXISTS idx_recurring_patterns_chat_active
    ON recurring_patterns(chat_id, is_active);

CREATE TABLE IF NOT EXISTS recurring_instances (
    pattern_id TEXT NOT NULL,
    post_id TEXT NOT NULL UNIQUE,
    ordinal INTEGER NOT NULL,
    scheduled_for_utc INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (pattern_id, ordinal),
    CHECK (ordinal > 0),
    FOREIGN KEY (pattern_id) REFERENCES recurring_patterns(id) ON DELETE CASCADE,
    FOREIGN KEY (post_id) REFERENCES scheduled_posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recurring_instances_pattern_scheduled
    ON recurring_instances(pattern_id, scheduled_for_utc);
