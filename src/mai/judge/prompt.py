# src/mai/judge/prompt.py
import json

PROMPT_VERSION = 1

SYSTEM_PROMPT = (
    "You are a porting advisor for the getMaNGOS World of Warcraft emulator forks. "
    "You are shown a fix from a source fork and evidence about how it applies to a "
    "target fork: which patch hunks apply vs reject, the target's current code at "
    "those spots (best-effort, source-fork line numbers), similar commits already in "
    "the target, and the fix's intent. Judge ONLY from this evidence whether the fix "
    "belongs in the target. NEVER invent code, files, or commits you were not shown. "
    "Every tip and every citation MUST quote a file path, a 'path:line', or a commit "
    "sha that appears verbatim in the evidence — ungrounded claims are discarded and "
    "lower your confidence. If the evidence is insufficient, use assessment "
    "\"uncertain\". Respond with ONLY a single JSON object with keys: assessment "
    "(portable|already_handled|divergent|uncertain), confidence (0.0-1.0), reason "
    "(string), tips (list of strings), adapted_hunks (list of {path, suggestion}), "
    "citations (list of strings)."
)

_CAP = 24000


def build_prompt(evidence: dict) -> str:
    """Render the evidence packet compactly for the judge (capped)."""
    blob = json.dumps(evidence, separators=(",", ":"))[:_CAP]
    return "Review evidence (JSON):\n" + blob + "\n\nReturn the JSON opinion object."
