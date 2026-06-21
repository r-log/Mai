import asyncio
from typing import Protocol


class DeployHook(Protocol):
    """Rebuilds/publishes the live site after a refresh."""

    async def trigger(self) -> None: ...


class ShellDeployHook:
    """Runs a configured shell command (e.g. a build+upload script)."""

    def __init__(self, command: str) -> None:
        self._command = command

    async def trigger(self) -> None:
        proc = await asyncio.create_subprocess_shell(self._command)
        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(
                f"deploy command failed (exit {rc}): {self._command}")
