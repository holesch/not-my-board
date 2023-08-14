#!/usr/bin/env python3

import asyncio
import contextlib
import os
import pathlib
import sys

import not_my_board._jsonrpc as jsonrpc


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
        reserved_names = {e["place"] for e in await proxy.list()}
        if name in reserved_names:
            await proxy.attach(name)

            others = reserved_names - {name}
            if not keep_others and others:
                for other in others:
                    await proxy.return_reservation(name=other, force=True)
        else:
            spec_file = _find_spec_file(name)
            spec_name = spec_file.stem
            await proxy.reserve(spec_name, spec_file.as_posix())
            await proxy.attach(spec_name)

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
    busnum = (sysfs_path / "busnum").read_text().rstrip()
    devpath = (sysfs_path / "devpath").read_text().rstrip()

    busid = f"{busnum}-{devpath}"

    pipe = pathlib.Path("/run/usbip-refresh-" + busid)
    if pipe.exists():
        print(f"Binding to usbip-host: {busid}", file=sys.stderr)
        match_busid_path = pathlib.Path("/sys/bus/usb/drivers/usbip-host/match_busid")
        if not match_busid_path.exists():
            await _exec("modprobe", "usbip-host")
        match_busid_path.write_text(f"add {busid}")
        bind_path = pathlib.Path("/sys/bus/usb/drivers/usbip-host/bind")
        bind_path.write_text(busid)
        with pipe.open("r+b", buffering=0) as f:
            f.write(b".")
    else:
        print(f"Loading default driver: {busid}", file=sys.stderr)
        probe_path = pathlib.Path("/sys/bus/usb/drivers_probe")
        try:
            probe_path.write_text(busid)
        except OSError:
            # fails for USB Hubs
            pass


async def _exec(*args, **kwargs):
    proc = await asyncio.create_subprocess_exec(*args, **kwargs)
    await proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"{args!r} exited with {proc.returncode}")


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
                os.environ.get("XDG_CONFIG_HOME", home / ".config")
            )
            spec_file = config_home / "not-my-board" / "specs" / f"{name}.toml"
            if not spec_file.is_file():
                raise ValueError(f"No spec file exists for name {name}")

    return spec_file


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
