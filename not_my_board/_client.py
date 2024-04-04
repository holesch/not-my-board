#!/usr/bin/env python3

import asyncio
import contextlib
import os
import pathlib
import sys

import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._util as util


async def reserve(import_description, with_name=None):
    found_import_desc = _find_import_description(import_description, with_name)

    async with agent_channel() as agent:
        await agent.reserve(found_import_desc.dict())


async def return_reservation(name):
    async with agent_channel() as agent:
        await agent.return_reservation(name)


async def attach(name, keep_others=False):
    async with agent_channel() as agent:
        reserved_names = {e["place"] for e in await agent.list()}
        if name in reserved_names:
            await agent.attach(name)

            others = reserved_names - {name}
            if not keep_others and others:
                for other in others:
                    await agent.return_reservation(name=other, force=True)
        else:
            found_import_desc = _find_import_description(name)
            await agent.reserve(found_import_desc.dict())
            await agent.attach(found_import_desc.name)

            if not keep_others and reserved_names:
                for other in reserved_names:
                    await agent.return_reservation(name=other, force=True)


async def detach(name, keep=False):
    async with agent_channel() as agent:
        await agent.detach(name)
        if not keep:
            await agent.return_reservation(name)


async def list_():
    async with agent_channel() as agent:
        return await agent.list()


async def status():
    async with agent_channel() as agent:
        return await agent.status()


async def uevent(devpath):
    # devpath has a leading "/", so joining with the / operator doesn't
    # work
    sysfs_path = pathlib.Path("/sys" + devpath)
    devname = sysfs_path.name

    pipe = pathlib.Path("/run/usbip-refresh-" + devname)
    if pipe.exists():
        with pipe.open("r+b", buffering=0) as f:
            f.write(b".")
    else:
        print(f"Loading default driver: {devname}", file=sys.stderr)
        probe_path = pathlib.Path("/sys/bus/usb/drivers_probe")
        probe_path.write_text(devname)


def _find_import_description(name, with_name=None):
    if "/" in name:
        import_description_file = pathlib.Path(name)
    else:
        path = pathlib.Path()
        home = pathlib.Path.home()

        while path != home:
            import_description_file = path / ".not-my-board" / f"{name}.toml"
            if import_description_file.is_file():
                break

            if path != path.parent:
                path = path.parent
            else:
                # we're at '/', stop loop
                path = home
        else:
            config_home = pathlib.Path(
                os.environ.get("XDG_CONFIG_HOME", home / ".config")
            )
            import_description_file = config_home / "not-my-board" / f"{name}.toml"
            if not import_description_file.is_file():
                raise ValueError(f"No import description file exists for name {name}")

    reservation_name = import_description_file.stem if not with_name else with_name
    import_description_content = util.toml_loads(import_description_file.read_text())
    import_description = models.ImportDesc(
        name=reservation_name, **import_description_content
    )

    return import_description


@contextlib.asynccontextmanager
async def agent_channel():
    socket_path = pathlib.Path("/run") / "not-my-board-agent.sock"
    reader, writer = await asyncio.open_unix_connection(socket_path)

    async def send(data):
        writer.write(data + b"\n")
        await writer.drain()

    async with jsonrpc.Channel(send, reader) as channel:
        yield channel
