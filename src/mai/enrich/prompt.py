from mai.enrich.schema import EnrichmentInput

PROMPT_VERSION = 1

SYSTEM_PROMPT = (
    "You are a bug-report normalizer for the MaNGOS World of Warcraft emulator "
    "project. Restructure and translate the report into clear, direct English. "
    "NEVER invent details that are not present in the source text. If the report "
    "is too sparse or ambiguous to understand, set needs_human_review to true and "
    "keep the summary strictly faithful to what is written. Only list affected "
    "entities (npc, zone, spell, item, quest) that are explicitly named in the "
    "source. Respond ONLY with a single JSON object matching the requested schema."
)


def build_prompt(ctx: EnrichmentInput) -> str:
    return (
        f"Core: {ctx.core}\n"
        f"Source: {ctx.source_type}\n"
        f"Title: {ctx.title}\n\n"
        f"Raw report:\n{ctx.raw_text}\n\n"
        "Return a JSON object with keys: normalized_title, english_summary, "
        "steps_to_reproduce (list), affected_entities (object with npc, zone, "
        "spell, item, quest lists), language_detected, severity_guess "
        "(low|medium|high|unknown), clarity_score (0.0-1.0), needs_human_review "
        "(boolean)."
    )
