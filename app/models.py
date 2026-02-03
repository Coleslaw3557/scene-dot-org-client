from pydantic import BaseModel


class CategoryOut(BaseModel):
    id: int
    name: str
    collection_count: int = 0
    track_count: int = 0


class TrackOut(BaseModel):
    id: int
    collection_id: int
    filename: str
    title: str
    format: str
    source_type: str
    file_size: int | None = None
    upvoted: bool = False
    play_count: int = 0


class CollectionOut(BaseModel):
    id: int
    category_id: int
    name: str
    remote_path: str
    art_url: str | None = None
    track_count: int = 0


class CollectionDetail(CollectionOut):
    tracks: list[TrackOut] = []
    category_name: str = ""


class PlayerState(BaseModel):
    track: TrackOut | None = None
    collection: CollectionOut | None = None
    category_name: str = ""
    history_position: int = 0
    has_prev: bool = False


class StatusOut(BaseModel):
    crawl_status: str = "idle"
    total_categories: int = 0
    total_collections: int = 0
    total_tracks: int = 0
    download_cache_mb: float = 0.0
    converted_cache_mb: float = 0.0
    upvoted_count: int = 0
