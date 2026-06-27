"""Gate 3 tested against the REAL #229 patch + real DBCFileLoader.cpp from the mirrors."""
from pathlib import Path

from mai.cppindex import extract
from mai.codememory.index import FileIndex
from mai.portability.patch import parse_patch
from mai.portability.symbols import missing_in_file

FIX = Path(__file__).parent / "fixtures"
PR229 = (FIX / "pr229.patch").read_text(encoding="utf-8")
THREE = (FIX / "dbcfileloader_three_229.cpp").read_bytes()   # source (post-image)
ZERO = (FIX / "dbcfileloader_zero.cpp").read_bytes()         # target


def _index(b: bytes) -> FileIndex:
    return FileIndex(
        exists=True,
        file_symbols=extract.file_symbols(b),
        functions=extract.functions(b),
    )


def test_patch_parses_single_cpp_file_with_added_lines():
    files = parse_patch(PR229)
    assert len(files) == 1
    fp = files[0]
    assert fp.new_path == "src/shared/DataStores/DBCFileLoader.cpp"
    assert fp.added_lines  # non-empty
    assert not fp.is_new_file and not fp.is_delete


def test_229_missing_loc_in_zero():
    fp = parse_patch(PR229)[0]
    missing = missing_in_file(
        source_bytes=THREE, target_index=_index(ZERO),
        added_lines=fp.added_lines, enclosing_name="AutoProduceStrings")
    names = {m.name for m in missing}
    assert "loc" in names, f"expected loc flagged, got {names}"


def test_229_does_not_flag_shared_context_symbols():
    """dataTable/stringPool/getRecord etc. resolve in BOTH forks -> never flagged."""
    fp = parse_patch(PR229)[0]
    missing = missing_in_file(
        source_bytes=THREE, target_index=_index(ZERO),
        added_lines=fp.added_lines, enclosing_name="AutoProduceStrings")
    names = {m.name for m in missing}
    for shared in ("dataTable", "stringPool", "stringTable", "getRecord"):
        assert shared not in names, f"false positive: {shared}"


def test_229_missing_evidence_is_specific():
    fp = parse_patch(PR229)[0]
    missing = missing_in_file(
        source_bytes=THREE, target_index=_index(ZERO),
        added_lines=fp.added_lines, enclosing_name="AutoProduceStrings")
    loc = next(m for m in missing if m.name == "loc")
    assert "parameter of AutoProduceStrings" in loc.detail
    assert "absent in target" in loc.detail


def test_no_missing_when_target_is_source_itself():
    """Porting #229 'into' Three (it already matches) surfaces no missing symbols."""
    fp = parse_patch(PR229)[0]
    missing = missing_in_file(
        source_bytes=THREE, target_index=_index(THREE),
        added_lines=fp.added_lines, enclosing_name="AutoProduceStrings")
    assert missing == []


def test_absent_target_returns_empty():
    """FileIndex(exists=False) -> no symbols to check -> []."""
    fp = parse_patch(PR229)[0]
    missing = missing_in_file(
        source_bytes=THREE, target_index=FileIndex(exists=False),
        added_lines=fp.added_lines, enclosing_name="AutoProduceStrings")
    assert missing == []


def test_none_target_returns_empty():
    """target_index=None -> []."""
    fp = parse_patch(PR229)[0]
    missing = missing_in_file(
        source_bytes=THREE, target_index=None,
        added_lines=fp.added_lines, enclosing_name="AutoProduceStrings")
    assert missing == []
