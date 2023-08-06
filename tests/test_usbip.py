import asyncio
import collections
import contextlib
import pathlib
import sys

import pytest

import not_my_board._util as util


class _VM:
    _name = ""

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(
                sh_task(f"./scripts/vmctl run {self._name}", f"vm {self._name}")
            )

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    async def configure(self):
        await sh(
            f"./scripts/vmctl configure {self._name}", prefix=f"configure {self._name}"
        )

    def ssh_task(self, cmd, *args, **kwargs):
        return sh_task(
            f"./scripts/vmctl ssh {self._name} while-stdin " + cmd,
            *args,
            terminate=False,
            **kwargs,
        )

    def ssh_task_root(self, cmd, *args, **kwargs):
        return sh_task(
            f"./scripts/vmctl ssh {self._name} doas while-stdin " + cmd,
            *args,
            terminate=False,
            **kwargs,
        )

    async def ssh(self, cmd, *args, **kwargs):
        return await sh(f"./scripts/vmctl ssh {self._name} " + cmd, *args, **kwargs)

    async def ssh_poll(self, cmd, timeout=None):
        return await sh_poll(f"./scripts/vmctl ssh {self._name} " + cmd, timeout)


class ServerVM(_VM):
    _name = "server"
    ip = "192.168.200.1"


class ExporterVM(_VM):
    _name = "exporter"
    ip = "192.168.200.2"

    async def usb_attach(self):
        await sh("./scripts/vmctl usb attach")

    async def usb_detach(self):
        await sh("./scripts/vmctl usb detach")


class ClientVM(_VM):
    _name = "client"
    ip = "192.168.200.3"


VMs = collections.namedtuple("VMs", ["server", "exporter", "client"])


@pytest.fixture(scope="session")
async def vms():
    async with ServerVM() as server:
        while True:
            try:
                async with util.connect("127.0.0.1", 5001):
                    pass
                async with util.connect("127.0.0.1", 5002):
                    pass
            except ConnectionRefusedError:
                await asyncio.sleep(0.1)
                continue
            break
        async with ExporterVM() as exporter:
            async with ClientVM() as client:
                await util.run_concurrently(
                    server.configure(),
                    exporter.configure(),
                    client.configure(),
                )
                await exporter.usb_attach()
                yield VMs(server, exporter, client)


async def test_raw_usb_forwarding(vms):
    async with vms.exporter.ssh_task_root(
        "python3 -m not_my_board._usbip export 2-1", "usbip export"
    ):
        # wait for listening socket
        await vms.exporter.ssh_poll("nc -z 127.0.0.1 3240")

        async with vms.client.ssh_task(
            f"python3 -m not_my_board._usbip import {vms.exporter.ip} 2-1 0",
            "usbip import",
        ):
            # wait for USB device to appear
            await vms.client.ssh_poll("test -e /dev/usbdisk")

            await vms.client.ssh("doas mount /media/usb")
            try:
                result = await vms.client.ssh("cat /media/usb/hello")
                assert result.stdout == "Hello, World!"
            finally:
                await vms.client.ssh("doas umount /media/usb")

    await vms.client.ssh("! test -e /sys/bus/usb/devices/2-1")


async def test_usb_forwarding(vms):
    async with vms.server.ssh_task("not-my-board serve", "serve"):
        # wait for listening socket
        await vms.server.ssh_poll("nc -z 127.0.0.1 2092")

        async with vms.exporter.ssh_task_root(
            f"not-my-board export http://{vms.server.ip}:2092 ./src/tests/qemu-usb-place.toml",
            "export",
        ):
            await vms.client.ssh("""'rm -f "$XDG_RUNTIME_DIR/not-my-board.sock"'""")
            async with vms.client.ssh_task(
                f"not-my-board agent http://{vms.server.ip}:2092", "agent"
            ):
                # wait until exported place is registered
                await vms.client.ssh_poll(
                    "wget -q -O - http://192.168.200.1:2092/api/v1/places | grep -q qemu-usb"
                )
                # wait until agent is ready
                await vms.client.ssh_poll(
                    """'test -e "$XDG_RUNTIME_DIR/not-my-board.sock"'"""
                )

                await vms.client.ssh("not-my-board attach ./src/tests/qemu-usb.toml")
                # TODO attach still returns before the device is available.
                # would be nice if it blocks until the device is ready.
                await vms.client.ssh_poll("test -e /sys/bus/usb/devices/2-1")
                try:
                    await vms.exporter.usb_detach()
                    await vms.client.ssh_poll("! test -e /sys/bus/usb/devices/2-1")
                finally:
                    await vms.exporter.usb_attach()

                await vms.client.ssh_poll("test -e /sys/bus/usb/devices/2-1")
                await vms.client.ssh("not-my-board detach qemu-usb")
                await vms.client.ssh("! test -e /sys/bus/usb/devices/2-1")


ShResult = collections.namedtuple("ShResult", ["stdout", "stderr", "returncode"])


async def sh(cmd, check=True, strip=True, prefix=None):
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    stdout, _ = await util.run_concurrently(
        proc.stdout.read(), _log_output(proc.stderr, cmd, prefix)
    )
    await proc.wait()
    if check and proc.returncode:
        raise RuntimeError(f"{cmd!r} exited with {proc.returncode}")

    stdout = stdout.decode("utf-8")
    if strip:
        stdout = stdout.rstrip()

    return ShResult(stdout, None, proc.returncode)


@contextlib.asynccontextmanager
async def sh_task(cmd, prefix=None, terminate=True):
    # need to exec, otherwise only the shell process is killed with
    # proc.terminate()
    proc = await asyncio.create_subprocess_shell(
        f"exec {cmd}",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    logging_task = None
    try:
        logging_task = asyncio.create_task(_log_output(proc.stdout, cmd, prefix))
        yield
    finally:
        proc.stdin.close()
        await proc.stdin.wait_closed()

        if terminate:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()

        await proc.wait()
        if logging_task:
            await logging_task


async def sh_poll(cmd, timeout=None):
    if pathlib.Path("/dev/kvm").exists():
        if timeout is None:
            timeout = 7
        interval = 0.1
    else:
        if timeout is None:
            timeout = 60
        interval = 1

    async def poll_loop():
        while True:
            result = await sh(cmd, check=False)
            if result.returncode == 0:
                break
            await asyncio.sleep(interval)

    await asyncio.wait_for(poll_loop(), timeout)


async def _log_output(stream, cmd, prefix):
    if prefix is None:
        prefix = f"[{cmd}] ".encode()
    else:
        prefix = f"[{prefix}] ".encode()

    async for line in stream:
        sys.stderr.buffer.write(prefix + line)
        sys.stderr.buffer.flush()
