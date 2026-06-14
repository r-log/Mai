from dataclasses import dataclass, field


@dataclass(frozen=True)
class IntakeEvent:
    """The single shape every source adapter emits (invariant 4)."""
    source_type: str          # ips | gh_issue | gh_pr | gh_commit
    source_id: str            # immutable id, e.g. "r1842"
    title: str
    core: str
    status: str = "open"
    repo_full_name: str | None = None
    raw_payload: dict = field(default_factory=dict)

    def canonical_key(self) -> str:
        return f"{self.source_type}:{self.source_id}"
