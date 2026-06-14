import re
from dataclasses import dataclass

_URL_RE = re.compile(r"/bug-tracker/([^/]+)/(?:.*/)?[^/]*-r(\d+)/?$")


def parse_bug_url(url: str) -> tuple[str, str]:
    """Return (core, 'rNNNN') for an IPS bug URL. Raises ValueError otherwise."""
    m = _URL_RE.search(url)
    if not m:
        raise ValueError(f"not an IPS bug url: {url}")
    segment, number = m.group(1), m.group(2)
    core = segment[len("mangos-"):] if segment.startswith("mangos-") else segment
    return core, f"r{number}"


@dataclass(frozen=True)
class IpsBug:
    title: str
    status: str | None
    main_category: str | None
    sub_category: str | None
    version: str | None
    milestone: str | None
    priority: str | None
    implemented_version: str | None


def _find(pattern: str, text: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def parse_bug_page(markdown: str) -> IpsBug:
    """Best-effort extraction of the labeled IPS fields from page markdown."""
    return IpsBug(
        title=_find(r"^#\s+(.+)$", markdown, re.M) or "",
        status=_find(r"Status:\s*([A-Za-z][A-Za-z ]*)", markdown),
        # NOTE: main/sub category use greedy (.+) to end-of-line; if Firecrawl ever
        # strips the trailing newline, switch to lazy (.+?) + a label lookahead.
        main_category=_find(r"Main Category:\*{0,2}\s*(.+)", markdown),
        sub_category=_find(r"Sub-Category:\*{0,2}\s*(.+)", markdown),
        version=_find(
            r"(?<!Implemented )Version:\*{0,2}\s*(.+?)\s*(?:Milestone:|Priority:|$)",
            markdown,
        ),
        milestone=_find(r"Milestone:\s*(\w+?)(?=Priority:|\n|$)", markdown),
        priority=_find(r"Priority:\s*(\w+)", markdown),
        implemented_version=_find(r"Implemented Version:\*{0,2}\s*(\w+)", markdown),
    )
