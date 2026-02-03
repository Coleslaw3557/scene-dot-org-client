# scene-music

A shuffle player for the [scene.org](https://www.scene.org/) demoscene music archive. Crawls the mirror catalog, builds a searchable database, and streams tracks through a web UI with format conversion handled server-side.

Supports MOD/XM/IT/S3M tracker formats (via ffmpeg + libopenmpt), SID files (via sidplayfp), and standard audio (MP3, OGG, FLAC, WAV).

## Screenshot
<img src="screenshot.png" width="300">

## Dependencies

- Python 3.11+
- ffmpeg built with `--enable-libopenmpt` (for tracker module playback)
- sidplayfp (optional, for C64 SID files)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 9000
```

### systemd service

```bash
sudo cp scene-music.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now scene-music
```

Edit `scene-music.service` to adjust `User`, `WorkingDirectory`, and port as needed.

## Project structure

```
app/
  main.py          # FastAPI app, startup, static files
  config.py        # Paths, constants, format lists
  database.py      # SQLite helpers (aiosqlite)
  models.py        # Pydantic schemas
  audio.py         # Download, convert, stream logic
  crawler.py       # Mirror crawler (populates music.db)
  routers/
    browse.py      # Browse/search/random endpoints
    player.py      # Stream, waveform, metadata endpoints
    upvote.py      # Save/unsave tracks
static/
  index.html       # Single-page UI
  style.css
  app.js
data/
  music.db         # Crawled catalog (~5 MB, tracked in git)
```
