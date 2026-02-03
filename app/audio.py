import asyncio
import io
import logging
import os
import shutil
import zipfile
from pathlib import Path

import httpx

from app.config import (
    DOWNLOAD_CACHE, CONVERTED_CACHE, DIRECT_STREAM_EXTENSIONS,
    TRACKER_EXTENSIONS, CONVERSION_MAX_DURATION, OGG_QUALITY,
    DOWNLOAD_CACHE_MAX, CONVERTED_CACHE_MAX,
)

log = logging.getLogger(__name__)


def cache_path_for_download(track_id: int, filename: str) -> Path:
    return DOWNLOAD_CACHE / f"{track_id}_{filename}"


def cache_path_for_converted(track_id: int) -> Path:
    return CONVERTED_CACHE / f"{track_id}.ogg"


def dir_size(path: Path) -> int:
    total = 0
    for f in path.iterdir():
        if f.is_file():
            total += f.stat().st_size
    return total


def evict_lru(cache_dir: Path, max_bytes: int):
    """Remove oldest files until under limit."""
    while dir_size(cache_dir) > max_bytes:
        files = sorted(cache_dir.iterdir(), key=lambda f: f.stat().st_atime)
        if not files:
            break
        oldest = files[0]
        log.info("Evicting cached file: %s", oldest.name)
        oldest.unlink(missing_ok=True)


async def download_file(url: str, dest: Path, client: httpx.AsyncClient) -> bool:
    """Download a file from the mirror."""
    if dest.exists():
        return True
    try:
        async with client.stream("GET", url, timeout=120.0) as resp:
            if resp.status_code != 200:
                log.warning("Download failed HTTP %d: %s", resp.status_code, url)
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
        return True
    except Exception as e:
        log.warning("Download error %s: %s", url, e)
        dest.unlink(missing_ok=True)
        return False


async def extract_from_zip(
    zip_url: str, path_in_zip: str, dest: Path, client: httpx.AsyncClient
) -> bool:
    """Download ZIP, extract a specific file."""
    if dest.exists():
        return True
    try:
        resp = await client.get(zip_url, timeout=120.0)
        if resp.status_code != 200:
            return False
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        data = zf.read(path_in_zip)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception as e:
        log.warning("ZIP extraction error: %s", e)
        dest.unlink(missing_ok=True)
        return False


async def convert_to_ogg(input_path: Path, output_path: Path) -> bool:
    """Convert tracker/other format to OGG using ffmpeg."""
    if output_path.exists():
        return True
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(input_path),
            "-t", str(CONVERSION_MAX_DURATION),
            "-c:a", "libvorbis", "-q:a", OGG_QUALITY,
            "-vn",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            log.warning("ffmpeg conversion failed: %s", stderr.decode()[-500:])
            output_path.unlink(missing_ok=True)
            return False
        return True
    except asyncio.TimeoutError:
        log.warning("ffmpeg conversion timed out for %s", input_path)
        output_path.unlink(missing_ok=True)
        return False
    except Exception as e:
        log.warning("Conversion error: %s", e)
        output_path.unlink(missing_ok=True)
        return False


async def prepare_track(track: dict) -> Path | None:
    """
    Ensure a track is available for streaming. Returns path to streamable file,
    or None on failure.

    track dict must have: id, filename, remote_url, format, source_type,
                          source_zip_url, path_in_zip
    """
    track_id = track["id"]
    filename = track["filename"]
    fmt = track["format"]
    source_type = track["source_type"]

    # Skip SID for now (no sidplayfp)
    if fmt == "sid":
        log.info("Skipping SID file (sidplayfp not available): %s", filename)
        return None

    dl_path = cache_path_for_download(track_id, filename)
    ogg_path = cache_path_for_converted(track_id)

    # If already converted, serve that
    if fmt in TRACKER_EXTENSIONS and ogg_path.exists():
        return ogg_path
    # If direct-streamable and cached
    if fmt in DIRECT_STREAM_EXTENSIONS and dl_path.exists():
        return dl_path

    # Evict if needed
    evict_lru(DOWNLOAD_CACHE, DOWNLOAD_CACHE_MAX)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Download/extract the raw file
        if source_type == "zip":
            ok = await extract_from_zip(
                track["source_zip_url"], track["path_in_zip"], dl_path, client
            )
        else:
            ok = await download_file(track["remote_url"], dl_path, client)

        if not ok:
            return None

    # For tracker formats, convert to OGG
    if fmt in TRACKER_EXTENSIONS:
        evict_lru(CONVERTED_CACHE, CONVERTED_CACHE_MAX)
        ok = await convert_to_ogg(dl_path, ogg_path)
        if not ok:
            return None
        return ogg_path

    # For wav/flac, also convert to OGG for smaller streaming
    if fmt in ("wav", "flac"):
        evict_lru(CONVERTED_CACHE, CONVERTED_CACHE_MAX)
        ok = await convert_to_ogg(dl_path, ogg_path)
        if not ok:
            return None
        return ogg_path

    # MP3/OGG: serve directly
    return dl_path


def content_type_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
    }.get(ext, "application/octet-stream")


async def get_original_file(track: dict) -> Path | None:
    """Download the original file for upvoting/saving."""
    dl_path = cache_path_for_download(track["id"], track["filename"])
    if dl_path.exists():
        return dl_path

    async with httpx.AsyncClient(follow_redirects=True) as client:
        if track["source_type"] == "zip":
            ok = await extract_from_zip(
                track["source_zip_url"], track["path_in_zip"], dl_path, client
            )
        else:
            ok = await download_file(track["remote_url"], dl_path, client)

    return dl_path if ok else None
