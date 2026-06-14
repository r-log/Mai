from mai.publish.areas import AREAS, area_of


def test_areas_palette_has_other_and_unique_slugs():
    slugs = [a["slug"] for a in AREAS]
    assert "other" in slugs
    assert len(slugs) == len(set(slugs))  # no dup slugs
    assert all(a.get("color") for a in AREAS)  # every area has a color


def test_area_from_ips_subcategory():
    # IPS sub-category "Pet" -> Creature
    assert area_of("Some title", None, {"sub_category": "Pet"}) == "Creature"
    assert area_of("x", None, {"sub_category": "Movement"}) == "Movement"


def test_area_from_enrichment_entities_when_no_category():
    enr = {"affected_entities": {"spell": ["Holy Light"], "npc": []}}
    assert area_of("ambiguous", enr, {}) == "Spell"


def test_area_from_title_keywords():
    assert area_of("Far teleport leaves player airborne", None, {}) == "Movement"
    assert area_of("Darkshore quest chain breaks", None, {}) == "Quest"


def test_area_defaults_to_other():
    assert area_of("totally unrelated wording", None, {}) == "Other"


def test_area_keyword_boundaries_avoid_substring_false_positives():
    # "mob" must not match "mobility"; "fly" must not match "firefly"
    assert area_of("player mobility was reduced", None, {}) == "Other"
    assert area_of("firefly is not lootable", None, {}) == "Loot"
