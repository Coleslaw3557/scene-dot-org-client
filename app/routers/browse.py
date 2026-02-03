import logging

from fastapi import APIRouter, HTTPException, Query

from app.database import get_db
from app.models import CategoryOut, CollectionOut, CollectionDetail, TrackOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["browse"])


@router.get("/categories")
async def list_categories() -> list[CategoryOut]:
    db = await get_db()
    try:
        cursor = await db.execute("""
            SELECT c.id, c.name,
                   (SELECT COUNT(*) FROM collections WHERE category_id=c.id) as collection_count,
                   (SELECT COUNT(*) FROM tracks t
                    JOIN collections col ON t.collection_id=col.id
                    WHERE col.category_id=c.id) as track_count
            FROM categories c ORDER BY c.name
        """)
        rows = await cursor.fetchall()
        return [
            CategoryOut(
                id=r["id"], name=r["name"],
                collection_count=r["collection_count"],
                track_count=r["track_count"],
            )
            for r in rows
        ]
    finally:
        await db.close()


@router.get("/collections")
async def list_collections(
    category: str | None = None,
    q: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[CollectionOut]:
    db = await get_db()
    try:
        conditions = []
        params: list = []

        if category:
            conditions.append(
                "col.category_id = (SELECT id FROM categories WHERE name=?)"
            )
            params.append(category)

        if q:
            conditions.append("col.name LIKE ?")
            params.append(f"%{q}%")

        where = "WHERE " + " AND ".join(conditions) if conditions else ""

        cursor = await db.execute(
            f"""SELECT col.* FROM collections col {where}
                ORDER BY col.name
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = await cursor.fetchall()
        return [
            CollectionOut(
                id=r["id"],
                category_id=r["category_id"],
                name=r["name"],
                remote_path=r["remote_path"],
                art_url=r["art_url"],
                track_count=r["track_count"],
            )
            for r in rows
        ]
    finally:
        await db.close()


@router.get("/collections/{collection_id}")
async def get_collection(collection_id: int) -> CollectionDetail:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM collections WHERE id=?", (collection_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Collection not found")

        cat_cursor = await db.execute(
            "SELECT name FROM categories WHERE id=?", (row["category_id"],)
        )
        cat_row = await cat_cursor.fetchone()

        tracks_cursor = await db.execute(
            "SELECT * FROM tracks WHERE collection_id=? ORDER BY filename",
            (collection_id,),
        )
        track_rows = await tracks_cursor.fetchall()

        tracks = [
            TrackOut(
                id=t["id"],
                collection_id=t["collection_id"],
                filename=t["filename"],
                title=t["title"],
                format=t["format"],
                source_type=t["source_type"],
                file_size=t["file_size"],
                upvoted=bool(t["upvoted"]),
                play_count=t["play_count"],
            )
            for t in track_rows
        ]

        return CollectionDetail(
            id=row["id"],
            category_id=row["category_id"],
            name=row["name"],
            remote_path=row["remote_path"],
            art_url=row["art_url"],
            track_count=row["track_count"],
            tracks=tracks,
            category_name=cat_row["name"] if cat_row else "",
        )
    finally:
        await db.close()
