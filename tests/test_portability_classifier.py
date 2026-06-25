from pathlib import Path

import pytest

from mai.git.fake import FakeGitClient
from mai.portability.classifier import cache_key, evaluate
from mai.portability.types import GATE_SUITE_VERSION, State

FIX = Path(__file__).parent / "fixtures"
PR229 = (FIX / "pr229.patch").read_text(encoding="utf-8")
THREE = (FIX / "dbcfileloader_three_229.cpp").read_text(encoding="utf-8")
ZERO = (FIX / "dbcfileloader_zero.cpp").read_text(encoding="utf-8")
PATH = "src/shared/DataStores/DBCFileLoader.cpp"

SRC, SHA, TGT, HEAD = "three", "78b7a9951", "zero", "zerohead0"


def _client(*, target_file: str, apply_forward: str = "clean",
            apply_reverse: str = "conflict") -> FakeGitClient:
    return FakeGitClient(
        diffs={(SRC, SHA): PR229},
        head_shas={TGT: HEAD},
        files={(SRC, SHA, PATH): THREE, (TGT, HEAD, PATH): target_file},
        apply_results={(TGT, PR229, False): apply_forward,
                       (TGT, PR229, True): apply_reverse},
    )


async def test_already_present_when_reverse_applies():
    c = _client(target_file=THREE, apply_reverse="reverse_clean")
    v = await evaluate(c, source_core=SRC, source_sha=SHA, target_core=TGT)
    assert v.state is State.ALREADY_PRESENT
    assert v.evidence[0].gate == "equivalence"


async def test_not_applicable_when_symbol_missing_despite_clean_apply():
    # The #229 trap: patch applies cleanly to Zero, but `loc` is absent there.
    c = _client(target_file=ZERO, apply_forward="clean")
    v = await evaluate(c, source_core=SRC, source_sha=SHA, target_core=TGT)
    assert v.state is State.NOT_APPLICABLE
    details = " ".join(e.detail for e in v.evidence)
    assert "loc" in details and "AutoProduceStrings" in details


async def test_portable_when_clean_and_symbols_present():
    # Target identical to source -> no missing symbols + clean apply.
    c = _client(target_file=THREE, apply_forward="clean")
    v = await evaluate(c, source_core=SRC, source_sha=SHA, target_core=TGT)
    assert v.state is State.PORTABLE
    assert v.confidence == "high"


async def test_adaptable_when_conflict_but_symbols_present():
    c = _client(target_file=THREE, apply_forward="conflict")
    v = await evaluate(c, source_core=SRC, source_sha=SHA, target_core=TGT)
    assert v.state is State.ADAPTABLE
    assert v.confidence == "medium"


async def test_not_applicable_when_file_absent():
    c = _client(target_file=ZERO, apply_forward="file_absent")
    v = await evaluate(c, source_core=SRC, source_sha=SHA, target_core=TGT)
    assert v.state is State.NOT_APPLICABLE
    assert any(e.result == "file_absent" for e in v.evidence)


def test_cache_key_includes_suite_version():
    assert cache_key("abc", "def") == ("abc", "def", GATE_SUITE_VERSION)
