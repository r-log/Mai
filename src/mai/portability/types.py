"""Verdict vocabulary for the portability classifier.

A verdict is an explicit STATE plus an evidence trail — never a boolean. Each gate
contributes Evidence; the terminal gate sets the state. See classifier.evaluate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Bump when gate logic changes so cached verdicts keyed on it are recomputed.
GATE_SUITE_VERSION = "g3.1"


class State(str, Enum):
    PORTABLE = "portable"             # absent, applies, required symbols present
    ADAPTABLE = "adaptable"           # relevant but won't apply verbatim (diverged context)
    NOT_APPLICABLE = "not_applicable"  # a construct the change depends on is absent
    ALREADY_PRESENT = "already_present"  # equivalent change already there
    UNCERTAIN = "uncertain"           # signals conflict/insufficient (no resolver in Phase 1)


@dataclass(frozen=True)
class Evidence:
    gate: str        # e.g. "equivalence", "mechanical_apply", "symbol_precondition"
    result: str      # short machine token, e.g. "reverse_clean", "missing_symbol"
    detail: str      # human-readable + specific, e.g. "param 'loc' absent in target"


@dataclass
class Verdict:
    state: State
    confidence: str = "high"          # high | medium
    evidence: list[Evidence] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "state": self.state.value,
            "confidence": self.confidence,
            "evidence": [
                {"gate": e.gate, "result": e.result, "detail": e.detail}
                for e in self.evidence
            ],
        }
