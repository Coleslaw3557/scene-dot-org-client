import logging
import shutil
from pathlib import Path
from urllib.parse import urlparse, unquote

from fastapi import APIRouter, HTTPException

from app.database import get_db
from app.audio import get_original_file
from app.config import UPVOTED_DIR

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/upvote", tags=["upvote"])


def _upvote_path(remote_url: str, filename: str) -> Path:
    """Build the upvoted/ tree path mirroring the server structure."""
    parsed = urlparse(remote_url)
    path_parts = unquote(parsed.path).strip("/").split("/")
    # Remove the leading pub/scene.org/music prefix
    if len(path_parts) > 3:
        path_parts = path_parts[3:]  # skip pub/scene.org/music
    # Use the directory structure, replace filename with actual filename
    if path_parts:
        path_parts[-1] = filename
    else:
        path_parts = [filename]
    return UPVOTED_DIR / Path(*path_parts)


@router.post("/{track_id}")
async def upvote_track(track_id: int):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tracks WHERE id=?", (track_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Track not found")

        track = dict(row)

        if track["upvoted"]:
            return {"status": "already_upvoted", "track_id": track_id}

        # Download original file
        original = await get_original_file(track)
        if original is None:
            raise HTTPException(503, "Failed to download original file")

        # Copy to upvoted tree
        dest = _upvote_path(track["remote_url"], track["filename"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(original), str(dest))

        # Mark as upvoted
        await db.execute("UPDATE tracks SET upvoted=1 WHERE id=?", (track_id,))
        await db.commit()

        log.info("Upvoted track %d -> %s", track_id, dest)
        return {"status": "upvoted", "track_id": track_id, "saved_to": str(dest)}
    finally:
        await db.close()


@router.delete("/{track_id}")
async def remove_upvote(track_id: int):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM tracks WHERE id=?", (track_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Track not found")

        track = dict(row)

        if not track["upvoted"]:
            return {"status": "not_upvoted", "track_id": track_id}

        # Remove from upvoted tree
        dest = _upvote_path(track["remote_url"], track["filename"])
        dest.unlink(missing_ok=True)

        # Clear flag
        await db.execute("UPDATE tracks SET upvoted=0 WHERE id=?", (track_id,))
        await db.commit()

        return {"status": "removed", "track_id": track_id}
    finally:
        await db.close()
