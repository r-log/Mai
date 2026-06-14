import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = 1


class EnrichmentSchemaError(ValueError):
    """Raised when a model's output is not valid against EnrichmentResult."""


class AffectedEntities(BaseModel):
    npc: list[str] = Field(default_factory=list)
    zone: list[str] = Field(default_factory=list)
    spell: list[str] = Field(default_factory=list)
    item: list[str] = Field(default_factory=list)
    quest: list[str] = Field(default_factory=list)


class EnrichmentResult(BaseModel):
    normalized_title: str
    english_summary: str
    steps_to_reproduce: list[str] = Field(default_factory=list)
    affected_entities: AffectedEntities = Field(default_factory=AffectedEntities)
    language_detected: str = "unknown"
    severity_guess: str = "unknown"
    clarity_score: float = 0.0
    needs_human_review: bool = False


def parse_enrichment(content: str | dict) -> EnrichmentResult:
    """Validate raw model output (JSON string or dict) into EnrichmentResult."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise EnrichmentSchemaError(f"invalid JSON: {exc}") from exc
    try:
        return EnrichmentResult.model_validate(content)
    except ValidationError as exc:
        raise EnrichmentSchemaError(str(exc)) from exc


@dataclass(frozen=True)
class EnrichmentInput:
    title: str
    core: str
    source_type: str
    raw_text: str

    def content_hash(self) -> str:
        blob = json.dumps(
            {"title": self.title, "core": self.core,
             "source_type": self.source_type, "raw_text": self.raw_text},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()


def raw_text_from_payload(source_type: str, payload: dict) -> str:
    """Extract the human-readable report text from a source_record payload."""
    if source_type == "ips":
        return payload.get("markdown", "") or ""
    return payload.get("body") or ""
