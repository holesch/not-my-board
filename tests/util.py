import asyncio
import collections
import contextlib
import pathlib
import sys

import not_my_board._jsonrpc as jsonrpc
import not_my_board._util as util

VMs = collections.namedtuple("VMs", ["hub", "exporter", "client"])
ShResult = collections.namedtuple("ShResult", ["stdout", "stderr", "returncode"])


class _VM(util.ContextStack):
    _name = ""

    async def _context_stack(self, stack):
        await stack.enter_async_context(
            sh_task(f"./scripts/vmctl run {self._name}", f"vm {self._name}")
        )

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


class HubVM(_VM):
    _name = "hub"


class ExporterVM(_VM):
    _name = "exporter"

    async def usb_attach(self):
        await sh("./scripts/vmctl usb attach")

    async def usb_detach(self):
        await sh("./scripts/vmctl usb detach")


class ClientVM(_VM):
    _name = "client"


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
async def sh_task(cmd, prefix=None, terminate=True, wait_ready=False):
    # need to exec, otherwise only the shell process is killed with
    # proc.terminate()
    proc = await asyncio.create_subprocess_shell(
        f"exec {cmd}",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE if wait_ready else asyncio.subprocess.STDOUT,
    )

    logging_task = None
    log_stream = proc.stderr if wait_ready else proc.stdout
    try:
        logging_task = asyncio.create_task(_log_output(log_stream, cmd, prefix))
        if wait_ready:
            await proc.stdout.readuntil(b"\n")
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


async def wait_for_ports(*ports, timeout=7):
    async with util.timeout(timeout):
        while True:
            try:
                for port in ports:
                    async with util.connect("localhost", port):
                        pass
            except (ConnectionRefusedError, OSError):
                await asyncio.sleep(0.1)
                continue
            break


def fake_rpc_pair():
    rpc1_to_rpc2 = asyncio.Queue()
    rpc2_to_rpc1 = asyncio.Queue()

    async def receive_iter(queue):
        while True:
            data = await queue.get()
            yield data
            queue.task_done()

    rpc1 = jsonrpc.Channel(rpc1_to_rpc2.put, receive_iter(rpc2_to_rpc1))
    rpc2 = jsonrpc.Channel(rpc2_to_rpc1.put, receive_iter(rpc1_to_rpc2))
    return rpc1, rpc2
