CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    team_id TEXT NULL,
    author_user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NULL,
    entities_json TEXT NULL,
    caption TEXT NULL,
    caption_entities_json TEXT NULL,
    caption_above INTEGER NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    CHECK (kind IN ('text', 'media')),
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (author_user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_drafts_author_created
    ON drafts(author_user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_drafts_team_created
    ON drafts(team_id, created_at);

CREATE TABLE IF NOT EXISTS draft_media (
    draft_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    type TEXT NOT NULL,
    file_id TEXT NOT NULL,
    PRIMARY KEY (draft_id, idx),
    FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
);
