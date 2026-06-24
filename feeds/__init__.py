from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class NewsItem:
    """Normalised news item emitted by any feed monitor."""
    headline:   str
    url:        str
    source:     str          # e.g. "Reuters", "AP", "r/worldnews"
    published:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    body:       str = ""     # summary / snippet if available
    item_id:    str = ""     # dedup hash
    retrieval_mode: str = "rss"
    source_hint_query: str = ""
    source_hint_domain: str = ""
