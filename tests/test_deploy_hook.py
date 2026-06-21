import pytest

from mai.refresh.deploy import ShellDeployHook


class _Proc:
    def __init__(self, rc: int) -> None:
        self._rc = rc

    async def wait(self) -> int:
        return self._rc


async def test_shell_deploy_hook_runs_command(monkeypatch):
    seen = []

    async def fake_shell(cmd):
        seen.append(cmd)
        return _Proc(0)

    monkeypatch.setattr(
        "mai.refresh.deploy.asyncio.create_subprocess_shell", fake_shell)
    await ShellDeployHook("deploy.sh").trigger()
    assert seen == ["deploy.sh"]


async def test_shell_deploy_hook_raises_on_failure(monkeypatch):
    async def fake_shell(cmd):
        return _Proc(2)

    monkeypatch.setattr(
        "mai.refresh.deploy.asyncio.create_subprocess_shell", fake_shell)
    with pytest.raises(RuntimeError):
        await ShellDeployHook("deploy.sh").trigger()
