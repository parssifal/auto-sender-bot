CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    timezone TEXT NULL,
    language TEXT NULL,
    username TEXT NULL,
    first_name TEXT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS destinations (
    chat_id INTEGER PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    username TEXT NULL,
    bot_status TEXT NOT NULL,
    bot_can_post INTEGER NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_destinations (
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    linked_via TEXT NOT NULL,
    linked_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, chat_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (chat_id) REFERENCES destinations(chat_id) ON DELETE CASCADE
);
