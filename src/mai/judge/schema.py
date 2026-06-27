import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class ReviewOpinionSchemaError(ValueError):
    """Raised when a model's output is not valid against ReviewOpinion."""


class AdaptedHunk(BaseModel):
    path: str
    suggestion: str


class ReviewOpinion(BaseModel):
    assessment: Literal["portable", "already_handled", "divergent", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    tips: list[str] = Field(default_factory=list)
    adapted_hunks: list[AdaptedHunk] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


def parse_opinion(content: str | dict) -> ReviewOpinion:
    """Validate raw model output (JSON string or dict) into ReviewOpinion."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ReviewOpinionSchemaError(f"invalid JSON: {exc}") from exc
    try:
        return ReviewOpinion.model_validate(content)
    except ValidationError as exc:
        raise ReviewOpinionSchemaError(str(exc)) from exc
