import asyncio
import io
import logging
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import (
    BASE_DIR, DOWNLOAD_CACHE, CONVERTED_CACHE, ART_CACHE, UPVOTED_DIR,
)
from app.database import init_db, get_db, get_state
from app.crawler import run_full_crawl
from app.models import StatusOut

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # Check if DB already has tracks — skip crawl if so
    db = await get_db()
    try:
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM tracks")
        row = await cursor.fetchone()
        track_count = row["cnt"]
    finally:
        await db.close()

    task = None
    if track_count > 0:
        log.info("DB has %d tracks, skipping crawl", track_count)
    else:
        log.info("DB is empty, starting crawl")
        task = asyncio.create_task(run_full_crawl())

    yield

    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="scene.org Music Discovery", lifespan=lifespan)

# Register routers
from app.routers.player import router as player_router
from app.routers.browse import router as browse_router
from app.routers.upvote import router as upvote_router

app.include_router(player_router)
app.include_router(browse_router)
app.include_router(upvote_router)


def _dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.iterdir() if f.is_file())
    return round(total / (1024 * 1024), 1)


@app.get("/api/status")
async def status() -> StatusOut:
    db = await get_db()
    try:
        crawl_status = await get_state("crawl_status", "idle")

        cat_c = await db.execute("SELECT COUNT(*) as cnt FROM categories")
        cat_row = await cat_c.fetchone()

        coll_c = await db.execute("SELECT COUNT(*) as cnt FROM collections")
        coll_row = await coll_c.fetchone()

        track_c = await db.execute("SELECT COUNT(*) as cnt FROM tracks")
        track_row = await track_c.fetchone()

        upvoted_c = await db.execute("SELECT COUNT(*) as cnt FROM tracks WHERE upvoted=1")
        upvoted_row = await upvoted_c.fetchone()

        return StatusOut(
            crawl_status=crawl_status,
            total_categories=cat_row["cnt"],
            total_collections=coll_row["cnt"],
            total_tracks=track_row["cnt"],
            download_cache_mb=_dir_size_mb(DOWNLOAD_CACHE),
            converted_cache_mb=_dir_size_mb(CONVERTED_CACHE),
            upvoted_count=upvoted_row["cnt"],
        )
    finally:
        await db.close()


@app.get("/api/art/{collection_id}")
async def get_art(collection_id: int):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT art_url FROM collections WHERE id=?", (collection_id,)
        )
        row = await cursor.fetchone()
        if not row or not row["art_url"]:
            raise HTTPException(404, "No art available")
        art_url = row["art_url"]
    finally:
        await db.close()

    # Check cache
    cache_file = ART_CACHE / f"{collection_id}.img"
    if cache_file.exists():
        # Detect content type from first bytes
        data = cache_file.read_bytes()
        ct = "image/png"
        if data[:3] == b"\xff\xd8\xff":
            ct = "image/jpeg"
        elif data[:4] == b"GIF8":
            ct = "image/gif"
        return Response(content=data, media_type=ct)

    # Fetch from server
    try:
        if art_url.startswith("zip:"):
            # Format: zip:<zip_url>!/<path_in_zip>
            rest = art_url[4:]
            zip_url, path_in_zip = rest.split("!/", 1)
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(zip_url, timeout=60.0)
                if resp.status_code != 200:
                    raise HTTPException(502, "Failed to fetch ZIP for art")
                zf = zipfile.ZipFile(io.BytesIO(resp.content))
                data = zf.read(path_in_zip)
        else:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(art_url, timeout=30.0)
                if resp.status_code != 200:
                    raise HTTPException(502, "Failed to fetch art")
                data = resp.content

        cache_file.write_bytes(data)
        ct = "image/png"
        if data[:3] == b"\xff\xd8\xff":
            ct = "image/jpeg"
        elif data[:4] == b"GIF8":
            ct = "image/gif"
        return Response(content=data, media_type=ct)

    except HTTPException:
        raise
    except Exception as e:
        log.warning("Art fetch error: %s", e)
        raise HTTPException(502, "Failed to fetch art")


# Serve index.html for root
@app.get("/")
async def root():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


# Mount static files (after explicit routes)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
