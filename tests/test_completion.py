import asyncio
import shlex
from typing import NamedTuple

import pytest

import not_my_board._util as util

WORKDIR = "/home/admin/completion"
PROMPT = "$ "
MARKER = "#"


@pytest.fixture(scope="module")
async def completion_farm(vms):
    """Board farm with a reserved place and files to complete"""

    nothing = "./src/tests/system_test/nothing.toml"
    await vms.client.ssh(
        shlex.quote(
            f"rm -rf {WORKDIR} .config/not-my-board"
            f" && mkdir -p {WORKDIR}/sub .config/not-my-board .zfunc"
            f" && cp {nothing} .config/not-my-board/board.toml"
            f" && cp {nothing} {WORKDIR}/myboard.toml"
            f" && cp {nothing} {WORKDIR}/sub/x.toml"
            " && not-my-board -s zsh > .zfunc/_not-my-board"
        )
    )

    async with (
        vms.hub.ssh_task("not-my-board hub", "hub", wait_ready=True),
        vms.exporter.ssh_task(
            "not-my-board export http://hub.local:2092 ./src/tests/system_test/place1.toml",
            "export1",
            wait_ready=True,
        ),
        vms.exporter.ssh_task(
            "not-my-board export http://hub.local:2092 ./src/tests/system_test/place2.toml",
            "export2",
            wait_ready=True,
        ),
        vms.client.ssh_task_root(
            "not-my-board agent http://hub.local:2092", "agent", wait_ready=True
        ),
    ):
        await vms.client.ssh(f"not-my-board reserve --with-name res1 {nothing}@place1")
        try:
            yield vms
        finally:
            await vms.client.ssh("tmux kill-server", check=False)
            await vms.client.ssh(
                shlex.quote(f"rm -rf {WORKDIR} .config/not-my-board .zfunc")
            )


_SHELL_SETUP = {
    "bash": [
        "bash --norc --noprofile",
        f"PS1='{PROMPT}'",
        "source <(not-my-board -s bash)",
        "bind 'set show-all-if-ambiguous on'",
    ],
    "zsh": [
        "zsh -f",
        f"PROMPT='{PROMPT}'",
        "fpath=(~/.zfunc $fpath)",
        "autoload -Uz compinit",
        "compinit -u",
    ],
}


class Completion(NamedTuple):
    line: str
    candidates: set


class Shell:
    """Interactive shell running in tmux on the client VM"""

    def __init__(self, vm, name):
        self._vm = vm
        self.name = name

    async def start(self):
        command, *setup = _SHELL_SETUP[self.name]
        await self._ssh(
            *["tmux", "new-session", "-d", "-s", self.name],
            *["-x", "200", "-y", "50", "-c", WORKDIR, command],
        )
        await self._run("; ".join(setup))

    async def reset(self):
        """Abort the current command line and clear the screen"""
        await self._tmux("send-keys", "C-c")
        await self._run("")

    async def _run(self, line):
        """Run the line, then clear the screen and wait until it's done"""
        await self._tmux("send-keys", "-l", f"{line}; clear" if line else "clear")
        await self._tmux("send-keys", "Enter")
        await self._wait_for(
            lambda: self._screen == [PROMPT.rstrip()], "a cleared screen"
        )

    async def complete(self, line):
        """Type the line, press Tab and return the result as a Completion"""
        await self.reset()
        await self._tmux("send-keys", "-l", line)
        await self._tmux("send-keys", "Tab")
        # Type a marker after Tab. The shell processes keys in order, so once
        # the marker shows up, the completion is done.
        await self._tmux("send-keys", "-l", MARKER)
        await self._wait_for(
            lambda: (self._line or "").endswith(MARKER), "completion to finish"
        )
        # The shell may add a space after a unique completion
        completed = self._line.removesuffix(MARKER).rstrip()
        return Completion(completed, self._candidates)

    @property
    def _line(self):
        prompt_lines = [line for line in self._screen if line.startswith(PROMPT)]
        return prompt_lines[-1][len(PROMPT) :] if prompt_lines else None

    @property
    def _candidates(self):
        lines = [line for line in self._screen if not line.startswith(PROMPT)]
        return {word for line in lines for word in line.split()}

    async def _wait_for(self, condition, description):
        try:
            async with util.timeout(7):
                while True:
                    await self._capture()
                    if condition():
                        return
        except asyncio.TimeoutError:
            screen = "\n".join(self._screen)
            pytest.fail(f"Timeout waiting for {description}, screen:\n{screen}")

    async def _capture(self):
        result = await self._tmux("capture-pane", "-p")
        self._screen = [line.rstrip() for line in result.stdout.splitlines() if line]

    async def _tmux(self, command, *args):
        return await self._ssh("tmux", command, "-t", self.name, *args)

    async def _ssh(self, *args):
        return await self._vm.ssh(shlex.quote(shlex.join(args)))


@pytest.fixture(scope="module", params=["bash", "zsh"])
async def shell(request, completion_farm):
    shell = Shell(completion_farm.client, request.param)
    await shell.start()
    return shell


def xfail_bash(request, shell):
    if shell.name == "bash":
        reason = '"@" is in COMP_WORDBREAKS, so shtab sees separate words'
        request.applymarker(pytest.mark.xfail(reason=reason, strict=True))


async def test_reservation_name(shell):
    completion = await shell.complete("not-my-board return r")
    assert completion.line == "not-my-board return res1"


async def test_import_description_name(shell):
    completion = await shell.complete("not-my-board search bo")
    assert completion.line == "not-my-board search board"


async def test_file_in_current_directory(shell):
    completion = await shell.complete("not-my-board search my")
    assert completion.line == "not-my-board search ./myboard.toml"


async def test_directory_in_current_directory(shell):
    completion = await shell.complete("not-my-board search su")
    assert completion.line == "not-my-board search ./sub/"


async def test_file_in_subdirectory(shell):
    completion = await shell.complete("not-my-board search sub/")
    assert completion.line == "not-my-board search sub/x.toml"


async def test_search_candidates(shell):
    completion = await shell.complete("not-my-board search ")
    assert completion.candidates == {"board", "myboard.toml", "sub/"}


async def test_attach_candidates(shell):
    completion = await shell.complete("not-my-board attach ")
    assert completion.candidates == {"board", "res1", "myboard.toml", "sub/"}


async def test_search_has_no_place_name(request, shell):
    xfail_bash(request, shell)
    completion = await shell.complete("not-my-board search board@")
    assert completion == Completion("not-my-board search board@", set())


@pytest.mark.parametrize("command", ["reserve", "attach"])
async def test_import_description_with_place_name(request, shell, command):
    xfail_bash(request, shell)
    completion = await shell.complete(f"not-my-board {command} board@p")
    assert completion.line == f"not-my-board {command} board@place"


async def test_place_name(request, shell):
    xfail_bash(request, shell)
    completion = await shell.complete("not-my-board show @p")
    assert completion.line == "not-my-board show @place"


async def test_candidates_are_not_expanded(completion_farm, shell):
    client = completion_farm.client
    malicious = shlex.quote(".config/not-my-board/$(id>pwned).toml")
    await client.ssh(shlex.quote(f"touch {malicious}"))
    try:
        completion = await shell.complete("not-my-board search bo")
        assert completion.line == "not-my-board search board"
        result = await client.ssh(f"test -e {WORKDIR}/pwned", check=False)
        assert result.returncode != 0
    finally:
        await client.ssh(shlex.quote(f"rm -f {malicious} {WORKDIR}/pwned"))


async def test_no_matching_place_name(shell):
    completion = await shell.complete("not-my-board reserve board@bo")
    assert completion == Completion("not-my-board reserve board@bo", set())
