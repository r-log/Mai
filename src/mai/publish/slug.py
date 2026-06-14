def safe_slug(key: str) -> str:
    """Filesystem/URL-safe slug for a canonical key. Single source of truth for
    both the page path (site.py) and the dashboard link (dataviz.py)."""
    return key.replace(":", "-").replace("/", "-").replace("#", "-")
