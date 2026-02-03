import asyncio
import logging
import re
import zipfile
import io
from datetime import datetime, timezone
from urllib.parse import urljoin, unquote

import httpx

from app.config import (
    MIRROR_BASE_URL, CATEGORIES, AUDIO_EXTENSIONS, ART_FILENAMES,
    CRAWL_CONCURRENCY, ZIP_INSPECT_MAX_SIZE,
)
from app.database import get_db, set_state

log = logging.getLogger(__name__)

# Matches Apache HTML table directory listing rows like:
# <td><a href="file.zip">file.zip</a></td><td align="right">14-Apr-2002 11:45  </td><td align="right"> 73K</td>
LISTING_RE = re.compile(
    r'<a href="([^"]+)">([^<]+)</a></td>'
    r'\s*<td[^>]*>\s*(\d{2}-\w{3}-\d{4}\s+\d{2}:\d{2})\s*</td>'
    r'\s*<td[^>]*>\s*([\d.]+[KMG]?|-)\s*</td>',
    re.IGNORECASE,
)


def parse_size(s: str) -> int | None:
    s = s.strip()
    if s == "-":
        return None
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
    for suffix, mult in multipliers.items():
        if s.upper().endswith(suffix):
            return int(float(s[:-1]) * mult)
    try:
        return int(s)
    except ValueError:
        return None


def clean_title(filename: str) -> str:
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name or filename


def parse_listing(html: str, base_url: str) -> tuple[list[dict], list[dict]]:
    """Parse Apache directory listing HTML. Returns (dirs, files)."""
    dirs = []
    files = []
    for match in LISTING_RE.finditer(html):
        href, _name, _date, size_str = match.groups()
        if href.startswith("?") or href.startswith("/"):
            continue
        decoded = unquote(href)
        full_url = urljoin(base_url, href)
        if href.endswith("/"):
            dirs.append({"name": decoded.rstrip("/"), "url": full_url})
        else:
            ext = decoded.rsplit(".", 1)[-1].lower() if "." in decoded else ""
            size = parse_size(size_str)
            files.append({
                "name": decoded,
                "url": full_url,
                "ext": ext,
                "size": size,
            })
    return dirs, files


async def fetch_listing(client: httpx.AsyncClient, url: str, semaphore: asyncio.Semaphore):
    """Fetch and parse a single directory listing."""
    async with semaphore:
        try:
            resp = await client.get(url, timeout=30.0)
            if resp.status_code != 200:
                log.warning("HTTP %d for %s", resp.status_code, url)
                return None, [], []
            dirs, files = parse_listing(resp.text, url)
            return resp.status_code, dirs, files
        except Exception as e:
            log.warning("Failed to fetch %s: %s", url, e)
            return None, [], []


async def crawl_collection(
    client: httpx.AsyncClient, url: str, category_id: int,
    collection_id: int, db, semaphore: asyncio.Semaphore,
    depth: int = 0, max_depth: int = 3,
):
    """Crawl inside a collection directory, inserting tracks."""
    if depth > max_depth:
        return

    status, dirs, files = await fetch_listing(client, url, semaphore)
    if status is None:
        return

    await db.execute(
        "INSERT OR REPLACE INTO crawl_log(url, crawled_at, status_code) VALUES(?, ?, ?)",
        (url, datetime.now(timezone.utc).isoformat(), status),
    )

    # Detect art
    art_url = None
    for f in files:
        if f["name"].lower() in ART_FILENAMES:
            art_url = f["url"]
            break

    # Insert tracks
    for f in files:
        if f["ext"] in AUDIO_EXTENSIONS:
            title = clean_title(f["name"])
            try:
                await db.execute(
                    """INSERT OR IGNORE INTO tracks
                       (collection_id, filename, title, remote_url, format, source_type, file_size)
                       VALUES (?, ?, ?, ?, ?, 'direct', ?)""",
                    (collection_id, f["name"], title, f["url"], f["ext"], f["size"]),
                )
            except Exception as e:
                log.warning("Failed to insert track %s: %s", f["name"], e)

        elif f["ext"] == "zip" and f["size"] and f["size"] <= ZIP_INSPECT_MAX_SIZE:
            await inspect_zip(client, f["url"], collection_id, db, semaphore)

    if art_url:
        await db.execute(
            "UPDATE collections SET art_url=? WHERE id=?", (art_url, collection_id)
        )

    # Update track count
    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM tracks WHERE collection_id=?", (collection_id,)
    )
    row = await cursor.fetchone()
    await db.execute(
        "UPDATE collections SET track_count=?, crawled_at=? WHERE id=?",
        (row["cnt"], datetime.now(timezone.utc).isoformat(), collection_id),
    )
    await db.commit()

    # Recurse into subdirectories (still same collection)
    for d in dirs:
        await crawl_collection(
            client, d["url"], category_id, collection_id, db, semaphore,
            depth + 1, max_depth,
        )


async def crawl_one_collection(
    client: httpx.AsyncClient, category_id: int,
    dir_entry: dict, db, semaphore: asyncio.Semaphore,
):
    """Create one collection row, then crawl its contents."""
    await db.execute(
        """INSERT OR IGNORE INTO collections(category_id, name, remote_path)
           VALUES(?, ?, ?)""",
        (category_id, dir_entry["name"], dir_entry["url"]),
    )
    await db.commit()

    cursor = await db.execute(
        "SELECT id FROM collections WHERE remote_path=?", (dir_entry["url"],)
    )
    row = await cursor.fetchone()
    if row:
        await crawl_collection(
            client, dir_entry["url"], category_id, row["id"], db, semaphore,
        )


async def crawl_category(
    client: httpx.AsyncClient, cat_name: str, cat_id: int,
    db, semaphore: asyncio.Semaphore,
):
    """Crawl one category: fetch its listing, then crawl all sub-collections concurrently."""
    cat_url = MIRROR_BASE_URL + cat_name + "/"
    log.info("Crawling category: %s", cat_name)

    status, dirs, files = await fetch_listing(client, cat_url, semaphore)
    if status is None:
        log.warning("Failed to fetch category listing: %s", cat_name)
        return

    await db.execute(
        "INSERT OR REPLACE INTO crawl_log(url, crawled_at, status_code) VALUES(?, ?, ?)",
        (cat_url, datetime.now(timezone.utc).isoformat(), status),
    )
    await db.commit()

    # Fan out: crawl all subdirectories (collections) concurrently
    tasks = []
    for d in dirs:
        tasks.append(
            crawl_one_collection(client, cat_id, d, db, semaphore)
        )

    # Run in batches to avoid overwhelming things
    batch_size = 20
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        await asyncio.gather(*batch, return_exceptions=True)
        # Be polite to the server
        await asyncio.sleep(0.2)
        # Log progress
        done = min(i + batch_size, len(tasks))
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM tracks")
        row = await cursor.fetchone()
        log.info("  %s: %d/%d collections crawled, %d tracks total",
                 cat_name, done, len(tasks), row["cnt"])

    # Any audio files at category level become a misc collection
    audio_at_root = [f for f in files if f["ext"] in AUDIO_EXTENSIONS]
    if audio_at_root:
        misc_name = f"_misc_{cat_name}"
        await db.execute(
            """INSERT OR IGNORE INTO collections(category_id, name, remote_path)
               VALUES(?, ?, ?)""",
            (cat_id, misc_name, cat_url),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT id FROM collections WHERE remote_path=?", (cat_url,)
        )
        row = await cursor.fetchone()
        if row:
            for f in audio_at_root:
                title = clean_title(f["name"])
                await db.execute(
                    """INSERT OR IGNORE INTO tracks
                       (collection_id, filename, title, remote_url, format, source_type, file_size)
                       VALUES (?, ?, ?, ?, ?, 'direct', ?)""",
                    (row["id"], f["name"], title, f["url"], f["ext"], f["size"]),
                )
            await db.execute(
                "UPDATE collections SET track_count=? WHERE id=?",
                (len(audio_at_root), row["id"]),
            )
            await db.commit()


async def inspect_zip(
    client: httpx.AsyncClient, zip_url: str, collection_id: int,
    db, semaphore: asyncio.Semaphore,
):
    """Download a small ZIP and catalog audio files inside it."""
    async with semaphore:
        try:
            resp = await client.get(zip_url, timeout=60.0)
            if resp.status_code != 200:
                return
        except Exception as e:
            log.warning("Failed to download ZIP %s: %s", zip_url, e)
            return

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.split("/")[-1]
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in AUDIO_EXTENSIONS:
                title = clean_title(name)
                remote_key = f"{zip_url}!/{info.filename}"
                try:
                    await db.execute(
                        """INSERT OR IGNORE INTO tracks
                           (collection_id, filename, title, remote_url, format,
                            source_type, source_zip_url, path_in_zip, file_size)
                           VALUES (?, ?, ?, ?, ?, 'zip', ?, ?, ?)""",
                        (collection_id, name, title, remote_key, ext,
                         zip_url, info.filename, info.file_size),
                    )
                except Exception as e:
                    log.warning("Failed to insert ZIP track %s: %s", name, e)

            # Check for art inside ZIP
            lower = name.lower()
            if lower in ART_FILENAMES:
                await db.execute(
                    "UPDATE collections SET art_url=? WHERE id=? AND art_url IS NULL",
                    (f"zip:{zip_url}!/{info.filename}", collection_id),
                )

        await db.commit()
    except zipfile.BadZipFile:
        log.warning("Bad ZIP file: %s", zip_url)
    except Exception as e:
        log.warning("Error inspecting ZIP %s: %s", zip_url, e)


async def run_full_crawl():
    """Crawl all categories from the mirror."""
    await set_state("crawl_status", "running")
    log.info("Starting full crawl of %s", MIRROR_BASE_URL)

    db = await get_db()
    semaphore = asyncio.Semaphore(CRAWL_CONCURRENCY)

    try:
        # Insert categories
        for cat_name in CATEGORIES:
            cat_url = MIRROR_BASE_URL + cat_name + "/"
            await db.execute(
                "INSERT OR IGNORE INTO categories(name, remote_path) VALUES(?, ?)",
                (cat_name, cat_url),
            )
        await db.commit()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            for cat_name in CATEGORIES:
                cursor = await db.execute(
                    "SELECT id FROM categories WHERE name=?", (cat_name,)
                )
                row = await cursor.fetchone()
                if not row:
                    continue
                await crawl_category(client, cat_name, row["id"], db, semaphore)

        # Final counts
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM tracks")
        row = await cursor.fetchone()
        log.info("Crawl complete. Total tracks: %d", row["cnt"])
        await set_state("crawl_status", "complete")
        await set_state("last_crawl", datetime.now(timezone.utc).isoformat())

    except Exception as e:
        log.error("Crawl failed: %s", e)
        await set_state("crawl_status", f"error: {e}")
        raise
    finally:
        await db.close()
