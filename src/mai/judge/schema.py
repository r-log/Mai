import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


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


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a model reply. Claude/Gemini ignore
    response_format=json_object and emit prose + a ```json fenced block (or a raw
    object); a bare json.loads chokes. Prefer the last fenced block, then narrow to
    the outermost {...} span."""
    s = text.strip()
    fences = _FENCE_RE.findall(s)
    if fences:
        s = fences[-1].strip()
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        s = s[start:end + 1]
    return s.strip()


def parse_opinion(content: str | dict) -> ReviewOpinion:
    """Validate raw model output (JSON string or dict) into ReviewOpinion."""
    if isinstance(content, str):
        try:
            content = json.loads(_extract_json(content))
        except json.JSONDecodeError as exc:
            raise ReviewOpinionSchemaError(f"invalid JSON: {exc}") from exc
    try:
        return ReviewOpinion.model_validate(content)
    except ValidationError as exc:
        raise ReviewOpinionSchemaError(str(exc)) from exc
