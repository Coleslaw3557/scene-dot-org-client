from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Remote server
MIRROR_BASE_URL = "http://128.237.157.9/pub/scene.org/music/"
CATEGORIES = ["artists", "groups", "compos", "compilations", "disks"]

# Local paths
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = BASE_DIR / "cache"
DOWNLOAD_CACHE = CACHE_DIR / "downloads"
CONVERTED_CACHE = CACHE_DIR / "converted"
ART_CACHE = CACHE_DIR / "art"
UPVOTED_DIR = BASE_DIR / "upvoted"
DB_PATH = DATA_DIR / "music.db"

# Cache limits (bytes)
DOWNLOAD_CACHE_MAX = 2 * 1024 * 1024 * 1024   # 2 GB
CONVERTED_CACHE_MAX = 1 * 1024 * 1024 * 1024   # 1 GB

# Crawler settings
CRAWL_CONCURRENCY = 5
ZIP_INSPECT_MAX_SIZE = 5 * 1024 * 1024  # 5 MB
CRAWL_REFRESH_HOURS = 24

# Audio settings
CONVERSION_MAX_DURATION = 600  # seconds (10 min cap for looping trackers)
OGG_QUALITY = "5"

# Supported formats
AUDIO_EXTENSIONS = {
    "mp3", "ogg", "wav", "flac",
    "mod", "xm", "it", "s3m", "stm", "mtm", "med", "669", "far", "ult",
    "sid",
}
TRACKER_EXTENSIONS = {
    "mod", "xm", "it", "s3m", "stm", "mtm", "med", "669", "far", "ult",
}
DIRECT_STREAM_EXTENSIONS = {"mp3", "ogg"}
ART_FILENAMES = {"cover.png", "cover.jpg", "cover.gif", "folder.png", "folder.jpg"}

# Shuffle settings
RECENT_REPEAT_WINDOW = 50  # avoid replaying last N tracks

# Ensure dirs exist
for d in [DATA_DIR, DOWNLOAD_CACHE, CONVERTED_CACHE, ART_CACHE, UPVOTED_DIR]:
    d.mkdir(parents=True, exist_ok=True)
