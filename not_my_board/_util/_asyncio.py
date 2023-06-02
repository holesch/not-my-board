import asyncio
import contextlib
import traceback

_RELAY_BUFFER_SIZE = 64 * 1024  # 64 KiB


async def run_concurrently(*coros):
    """Run coros concurrently and cancel others on error.

    Like `asyncio.gather()`, but if one task raises an exception, all other
    tasks are cancelled.
    """

    tasks = [asyncio.create_task(coro) for coro in coros]
    try:
        return await asyncio.gather(*tasks)
    finally:
        await cancel_tasks(tasks)


async def cancel_tasks(tasks):
    """Cancel tasks and wait until all are cancelled"""

    for task in tasks:
        if not task.done():
            task.cancel()

    for task in tasks:
        if not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass


@contextlib.asynccontextmanager
async def connect(*args, **kwargs):
    """Wraps `asyncio.open_connection()` in a context manager

    The connection is closed when leaving the context.
    """

    reader, writer = await asyncio.open_connection(*args, **kwargs)
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


async def relay_streams(client_r, client_w, remote_r, remote_w):
    """Relay data between two streams"""

    async def relay(reader, writer):
        while True:
            data = await reader.read(_RELAY_BUFFER_SIZE)
            if not data:
                writer.write_eof()
                return

            writer.write(data)
            await writer.drain()

    await run_concurrently(relay(client_r, remote_w), relay(remote_r, client_w))


class Server:
    """Wraps `asyncio.start_server()` and cleans up open connections

    When leaving the context, not only are the listening sockets closed,
    but all currently runnning connection handler callbacks are also
    cancelled.

    It also wrapps the connection handler to catch and log every exception
    raised in the handler and also closes the connection, when the handler
    function returns.
    """

    def __init__(self, connection_handler, *args, **kwargs):
        self._connection_handler = connection_handler
        self._args = args
        self._kwargs = kwargs
        self._tasks = set()

    async def __aenter__(self):
        self._server = await self._start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._server.close()
        await self._server.wait_closed()
        await cancel_tasks(self._tasks.copy())

    def _on_connect(self, reader, writer):
        task = asyncio.create_task(self._run_handler(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_handler(self, reader, writer):
        try:
            await self._connection_handler(reader, writer)
        except Exception:
            traceback.print_exc()
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _start(self):
        return await asyncio.start_server(self._on_connect, *self._args, **self._kwargs)

    async def serve_forever(self):
        await self._server.serve_forever()


class UnixServer(Server):
    """Same as `Server`, but for `asyncio.start_unix_server()`"""

    async def _start(self):
        return await asyncio.start_unix_server(
            self._on_connect, *self._args, **self._kwargs
        )