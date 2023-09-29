#!/usr/bin/env python3

import asyncio
import contextlib
import os
import pathlib
import sys

import not_my_board._jsonrpc as jsonrpc


async def reserve(import_description, with_name=None):
    import_description_file = _find_import_description(import_description)
    reservation_name = import_description_file.stem if not with_name else with_name

    async with agent_proxy() as proxy:
        await proxy.reserve(reservation_name, import_description_file.as_posix())


async def return_reservation(name):
    async with agent_proxy() as proxy:
        await proxy.return_reservation(name)


async def attach(name, keep_others=False):
    async with agent_proxy() as proxy:
        reserved_names = {e["place"] for e in await proxy.list()}
        if name in reserved_names:
            await proxy.attach(name)

            others = reserved_names - {name}
            if not keep_others and others:
                for other in others:
                    await proxy.return_reservation(name=other, force=True)
        else:
            import_description_file = _find_import_description(name)
            reservation_name = import_description_file.stem
            await proxy.reserve(reservation_name, import_description_file.as_posix())
            await proxy.attach(reservation_name)

            if not keep_others and reserved_names:
                for other in reserved_names:
                    await proxy.return_reservation(name=other, force=True)


async def detach(name, keep=False):
    async with agent_proxy() as proxy:
        await proxy.detach(name)
        if not keep:
            await proxy.return_reservation(name)


async def list_():
    async with agent_proxy() as proxy:
        return await proxy.list()


async def status():
    async with agent_proxy() as proxy:
        return await proxy.status()


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


def _find_import_description(name):
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

    return import_description_file


@contextlib.asynccontextmanager
async def agent_proxy():
    runtime_dir = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])
    reader, writer = await asyncio.open_unix_connection(
        runtime_dir / "not-my-board.sock"
    )

    async def send(data):
        writer.write(data + b"\n")
        await writer.drain()

    async with jsonrpc.Proxy(send, reader) as proxy:
        yield proxy
