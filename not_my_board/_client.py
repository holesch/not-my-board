#!/usr/bin/env python3

import asyncio
import not_my_board._jsonrpc as jsonrpc
import pathlib
import os
import contextlib


async def reserve(name, with_name=None):
    spec_file = _find_spec_file(name)
    spec_name = spec_file.stem if not with_name else with_name

    async with agent_proxy() as proxy:
        await proxy.reserve(spec_name, spec_file.as_posix())


async def return_reservation(name):
    async with agent_proxy() as proxy:
        await proxy.return_reservation(name)


async def attach(name, keep_others=False):
    async with agent_proxy() as proxy:
        reserved_names = set(await proxy.list())
        if name in reserved_names:
            await proxy.attach(name)
        else:
            spec_file = _find_spec_file(name)
            spec_name = spec_file.stem
            await proxy.reserve(spec_name, spec_file.as_posix())
            await proxy.attach(spec_name)


async def detach(name, keep=False):
    async with agent_proxy() as proxy:
        await proxy.detach(name)
        if not keep:
            await proxy.return_reservation(name)


async def list():
    async with agent_proxy() as proxy:
        return await proxy.list()


def _find_spec_file(name):
    if "/" in name:
        spec_file = pathlib.Path(name)
    else:
        path = pathlib.Path()
        home = pathlib.Path.home()

        while path != home:
            spec_file = path / ".not-my-board" / "specs" / f"{name}.toml"
            if spec_file.is_file():
                break

            if path != path.parent:
                path = path.parent
            else:
                # we're at '/', stop loop
                path = home
        else:
            config_home = pathlib.Path(
                    os.environ.get("XDG_CONFIG_HOME", home / ".config"))
            spec_file = config_home / "not-my-board" / "specs" / f"{name}.toml"
            if not spec_file.is_file():
                raise ValueError(f"No spec file exists for name {name}")

    return spec_file


@contextlib.asynccontextmanager
async def agent_proxy():
    runtime_dir = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])
    reader, writer = await asyncio.open_unix_connection(runtime_dir / "not-my-board.sock")

    async def send(data):
        writer.write(data + b"\n")
        await writer.drain()

    async with jsonrpc.Proxy(send, reader) as proxy:
        yield proxy
