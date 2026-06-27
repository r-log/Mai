import asyncio

import pytest

from mai.git.client import LocalGitClient


def _client(tmp_path):
    return LocalGitClient(str(tmp_path / "mirrors"), str(tmp_path / "wt"))


def test_wt_lock_is_per_core(tmp_path):
    c = _client(tmp_path)
    assert c._wt_lock("a") is c._wt_lock("a")
    assert c._wt_lock("a") is not c._wt_lock("b")


async def test_rejected_hunks_serializes_same_core(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(c, "ensure_worktree", lambda core: _aval("wt"))
    active = {"n": 0, "max": 0}

    async def fake_run_raw(args, *, stdin=None):
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        await asyncio.sleep(0.02)            # hold the "worktree window" open
        active["n"] -= 1
        return (0, "", "")
    monkeypatch.setattr(c, "_run_raw", fake_run_raw)

    # paths=[] -> no .rej file reads; two concurrent same-core calls must NOT overlap
    await asyncio.gather(c.rejected_hunks("four", "p", []),
                         c.rejected_hunks("four", "p", []))
    assert active["max"] == 1                 # serialized by the per-core lock


async def test_rejected_hunks_overlaps_across_cores(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(c, "ensure_worktree", lambda core: _aval("wt"))
    active = {"n": 0, "max": 0}

    async def fake_run_raw(args, *, stdin=None):
        active["n"] += 1
        active["max"] = max(active["max"], active["n"])
        await asyncio.sleep(0.02)
        active["n"] -= 1
        return (0, "", "")
    monkeypatch.setattr(c, "_run_raw", fake_run_raw)

    await asyncio.gather(c.rejected_hunks("one", "p", []),
                         c.rejected_hunks("two", "p", []))
    assert active["max"] == 2                 # different cores -> overlap allowed


async def _aval(v):
    return v
