import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.database import get_db
from app.models import PlayerState, TrackOut, CollectionOut
from app.audio import prepare_track, content_type_for_path
from app.config import RECENT_REPEAT_WINDOW

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/player", tags=["player"])


async def _track_to_out(row) -> TrackOut:
    return TrackOut(
        id=row["id"],
        collection_id=row["collection_id"],
        filename=row["filename"],
        title=row["title"],
        format=row["format"],
        source_type=row["source_type"],
        file_size=row["file_size"],
        upvoted=bool(row["upvoted"]),
        play_count=row["play_count"],
    )


async def _collection_out(db, collection_id: int) -> CollectionOut | None:
    cursor = await db.execute(
        "SELECT * FROM collections WHERE id=?", (collection_id,)
    )
    row = await cursor.fetchone()
    if not row:
        return None
    return CollectionOut(
        id=row["id"],
        category_id=row["category_id"],
        name=row["name"],
        remote_path=row["remote_path"],
        art_url=row["art_url"],
        track_count=row["track_count"],
    )


async def _category_name(db, category_id: int) -> str:
    cursor = await db.execute("SELECT name FROM categories WHERE id=?", (category_id,))
    row = await cursor.fetchone()
    return row["name"] if row else ""


async def _build_player_state(db, track_row) -> PlayerState:
    track = await _track_to_out(track_row)
    collection = await _collection_out(db, track.collection_id)
    cat_name = await _category_name(db, collection.category_id) if collection else ""

    cursor = await db.execute("SELECT COUNT(*) as cnt FROM shuffle_history")
    row = await cursor.fetchone()
    total = row["cnt"]

    cursor2 = await db.execute(
        "SELECT MAX(id) as max_id FROM shuffle_history"
    )
    max_row = await cursor2.fetchone()

    # Check current position
    from app.database import get_state
    pos_str = await get_state("shuffle_position")
    current_pos = int(pos_str) if pos_str else (max_row["max_id"] if max_row and max_row["max_id"] else 0)

    # Has prev?
    cursor3 = await db.execute(
        "SELECT id FROM shuffle_history WHERE id < ? ORDER BY id DESC LIMIT 1",
        (current_pos,)
    )
    prev_row = await cursor3.fetchone()

    return PlayerState(
        track=track,
        collection=collection,
        category_name=cat_name,
        history_position=current_pos,
        has_prev=prev_row is not None,
    )


@router.get("/current")
async def get_current() -> PlayerState:
    db = await get_db()
    try:
        from app.database import get_state
        pos_str = await get_state("shuffle_position")

        if pos_str:
            cursor = await db.execute(
                """SELECT t.* FROM tracks t
                   JOIN shuffle_history sh ON sh.track_id = t.id
                   WHERE sh.id = ?""",
                (int(pos_str),)
            )
            row = await cursor.fetchone()
            if row:
                return await _build_player_state(db, row)

        # No current track - pick one
        return await _pick_next(db)
    finally:
        await db.close()


async def _pick_next(db, scope: str = "track", current_collection_id: int | None = None) -> PlayerState:
    """Pick a random next track, avoiding recent repeats."""
    # Get recent track IDs to avoid
    cursor = await db.execute(
        "SELECT track_id FROM shuffle_history ORDER BY id DESC LIMIT ?",
        (RECENT_REPEAT_WINDOW,)
    )
    recent = [r["track_id"] for r in await cursor.fetchall()]

    if scope == "collection" and current_collection_id:
        # Pick from a different collection
        if recent:
            placeholders = ",".join("?" * len(recent))
            query = f"""
                SELECT t.* FROM tracks t
                WHERE t.collection_id != ?
                AND t.id NOT IN ({placeholders})
                AND t.format != 'sid'
                ORDER BY RANDOM() LIMIT 1
            """
            params = [current_collection_id] + recent
        else:
            query = """
                SELECT t.* FROM tracks t
                WHERE t.collection_id != ?
                AND t.format != 'sid'
                ORDER BY RANDOM() LIMIT 1
            """
            params = [current_collection_id]
        cursor = await db.execute(query, params)
    else:
        if recent:
            placeholders = ",".join("?" * len(recent))
            query = f"""
                SELECT * FROM tracks
                WHERE id NOT IN ({placeholders})
                AND format != 'sid'
                ORDER BY RANDOM() LIMIT 1
            """
            cursor = await db.execute(query, recent)
        else:
            query = """
                SELECT * FROM tracks
                WHERE format != 'sid'
                ORDER BY RANDOM() LIMIT 1
            """
            cursor = await db.execute(query)

    row = await cursor.fetchone()

    if not row:
        # Fallback: any track
        cursor = await db.execute(
            "SELECT * FROM tracks WHERE format != 'sid' ORDER BY RANDOM() LIMIT 1"
        )
        row = await cursor.fetchone()

    if not row:
        return PlayerState()

    # Record in history
    await db.execute(
        "INSERT INTO shuffle_history(track_id, played_at) VALUES(?, ?)",
        (row["id"], datetime.now(timezone.utc).isoformat()),
    )
    await db.commit()

    # Get the new history entry ID
    cursor2 = await db.execute("SELECT last_insert_rowid() as lid")
    lid_row = await cursor2.fetchone()
    new_pos = lid_row["lid"]

    from app.database import set_state
    await set_state("shuffle_position", str(new_pos))

    return await _build_player_state(db, row)


@router.post("/next")
async def next_track(scope: str = Query("track", pattern="^(track|collection)$")) -> PlayerState:
    db = await get_db()
    try:
        # Get current collection for scope-aware skip
        from app.database import get_state
        pos_str = await get_state("shuffle_position")
        current_coll_id = None
        if pos_str:
            cursor = await db.execute(
                """SELECT t.collection_id FROM tracks t
                   JOIN shuffle_history sh ON sh.track_id = t.id
                   WHERE sh.id = ?""",
                (int(pos_str),)
            )
            row = await cursor.fetchone()
            if row:
                current_coll_id = row["collection_id"]

        return await _pick_next(db, scope, current_coll_id)
    finally:
        await db.close()


@router.post("/prev")
async def prev_track() -> PlayerState:
    db = await get_db()
    try:
        from app.database import get_state
        pos_str = await get_state("shuffle_position")
        if not pos_str:
            raise HTTPException(404, "No playback history")

        current_pos = int(pos_str)
        cursor = await db.execute(
            "SELECT * FROM shuffle_history WHERE id < ? ORDER BY id DESC LIMIT 1",
            (current_pos,)
        )
        prev = await cursor.fetchone()
        if not prev:
            raise HTTPException(404, "No previous track")

        from app.database import set_state
        await set_state("shuffle_position", str(prev["id"]))

        cursor2 = await db.execute("SELECT * FROM tracks WHERE id=?", (prev["track_id"],))
        track_row = await cursor2.fetchone()
        if not track_row:
            raise HTTPException(404, "Track not found")

        return await _build_player_state(db, track_row)
    finally:
        await db.close()


@router.get("/stream/{track_id}")
async def stream_track(track_id: int):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tracks WHERE id=?", (track_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Track not found")

        track = dict(row)

        # Increment play count
        await db.execute(
            "UPDATE tracks SET play_count = play_count + 1 WHERE id=?", (track_id,)
        )
        await db.commit()
    finally:
        await db.close()

    path = await prepare_track(track)
    if path is None or not path.exists():
        raise HTTPException(503, "Failed to prepare track for streaming")

    return FileResponse(
        path=str(path),
        media_type=content_type_for_path(path),
        filename=path.name,
    )
