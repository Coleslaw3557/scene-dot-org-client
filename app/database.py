import aiosqlite
from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    remote_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name TEXT NOT NULL,
    remote_path TEXT UNIQUE NOT NULL,
    parent_id INTEGER REFERENCES collections(id),
    art_url TEXT,
    track_count INTEGER DEFAULT 0,
    crawled_at TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id),
    filename TEXT NOT NULL,
    title TEXT NOT NULL,
    remote_url TEXT UNIQUE NOT NULL,
    format TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'direct',
    source_zip_url TEXT,
    path_in_zip TEXT,
    file_size INTEGER,
    upvoted INTEGER DEFAULT 0,
    play_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shuffle_history (
    id INTEGER PRIMARY KEY,
    track_id INTEGER NOT NULL REFERENCES tracks(id),
    played_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS crawl_log (
    url TEXT PRIMARY KEY,
    crawled_at TEXT NOT NULL DEFAULT (datetime('now')),
    etag TEXT,
    status_code INTEGER
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_tracks_collection ON tracks(collection_id);
CREATE INDEX IF NOT EXISTS idx_tracks_format ON tracks(format);
CREATE INDEX IF NOT EXISTS idx_tracks_upvoted ON tracks(upvoted);
CREATE INDEX IF NOT EXISTS idx_collections_category ON collections(category_id);
CREATE INDEX IF NOT EXISTS idx_shuffle_history_played ON shuffle_history(played_at);
"""


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        await db.commit()
    finally:
        await db.close()


async def get_state(key: str, default: str | None = None) -> str | None:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT value FROM app_state WHERE key=?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else default
    finally:
        await db.close()


async def set_state(key: str, value: str):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO app_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
            (key, value, value),
        )
        await db.commit()
    finally:
        await db.close()
