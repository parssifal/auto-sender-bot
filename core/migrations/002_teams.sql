CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_teams_owner_created
    ON teams(owner_user_id, created_at);

CREATE TABLE IF NOT EXISTS team_members (
    team_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (team_id, user_id),
    CHECK (role IN ('owner', 'editor', 'viewer')),
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_team_members_user_team
    ON team_members(user_id, team_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_members_single_owner
    ON team_members(team_id)
    WHERE role='owner';

CREATE TABLE IF NOT EXISTS team_invites (
    token TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_by_user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    accepted_by_user_id INTEGER NULL,
    accepted_at INTEGER NULL,
    CHECK (role IN ('editor', 'viewer')),
    CHECK (expires_at > created_at),
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (accepted_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_team_invites_team_created
    ON team_invites(team_id, created_at);

CREATE INDEX IF NOT EXISTS idx_team_invites_expires
    ON team_invites(expires_at, accepted_at);
