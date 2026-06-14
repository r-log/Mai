from mai.db.models import DriftObservation
from mai.publish.views import ReportBundle

SCHEMA_VERSION = 2


def _q(text: str) -> str:
    """Quote a front-matter string value."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_report_page(bundle: ReportBundle) -> str:
    r = bundle.report
    enr = bundle.enrichment or {}
    title = enr.get("normalized_title") or r.title or r.canonical_key
    ver = bundle.verification
    verdict = ver.verdict if ver else "open"
    confidence = ver.confidence if ver else 0.0

    lines = ["---", f"schema_version: {SCHEMA_VERSION}", f"id: {_q(r.canonical_key)}",
             f"title: {_q(title)}", f"core: {r.core}", f"area: {bundle.area}",
             f"status: {r.status}", f"verdict: {verdict}", f"confidence: {confidence}",
             "---", ""]

    summary = enr.get("english_summary")
    if summary:
        lines += ["## Summary", "", summary, ""]
    steps = enr.get("steps_to_reproduce") or []
    if steps:
        lines += ["## Steps to reproduce", ""] + [f"- {s}" for s in steps] + [""]
    entities = enr.get("affected_entities") or {}
    ent_lines = [f"- **{k}:** {', '.join(v)}" for k, v in entities.items() if v]
    if ent_lines:
        lines += ["## Affected", ""] + ent_lines + [""]
    if bundle.correlations:
        lines += ["## Evidence", ""]
        lines += [f"- `{key}` ({method}, score {score:.2f})"
                  for key, method, score in bundle.correlations]
        lines += [""]
    return "\n".join(lines).rstrip() + "\n"


def render_drift_page(fork_a: str, fork_b: str,
                      observations: list[DriftObservation]) -> str:
    title = f"Drift: {fork_a} vs {fork_b}"
    lines = ["---", f"schema_version: {SCHEMA_VERSION}", f"title: {_q(title)}",
             "type: drift", f"fork_a: {fork_a}", f"fork_b: {fork_b}", "---", "",
             f"# {title}", "",
             "| Subsystem | Shared | Diverged | Identical | Only A | Only B |",
             "|---|---|---|---|---|---|"]
    for o in sorted(observations, key=lambda o: o.diverged, reverse=True):
        lines.append(f"| {o.subsystem} | {o.shared} | {o.diverged} | {o.identical} "
                     f"| {o.only_a} | {o.only_b} |")
    return "\n".join(lines).rstrip() + "\n"


def render_home(counts: dict) -> str:
    lines = ["---", f'title: {_q("Mai — getMaNGOS Bug & Drift Observatory")}',
             "---", "", "# Mai — getMaNGOS Bug & Drift Observatory", "",
             f"- **Reports:** {counts['reports']}",
             f"- **Enriched:** {counts['enriched']}",
             f"- **Verdicts:** open {counts['open']} · likely_fixed {counts['likely_fixed']} "
             f"· fixed_confirmed {counts['fixed_confirmed']}",
             f"- **Drift pairs:** {counts['drift_pairs']}"]
    return "\n".join(lines).rstrip() + "\n"
