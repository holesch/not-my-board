import asyncio
import contextlib
import fcntl
import logging
import signal
import traceback

_RELAY_BUFFER_SIZE = 64 * 1024  # 64 KiB
logger = logging.getLogger(__name__)


def run(coro, debug=False):
    def signal_handler(task):
        task.cancel()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if debug is not None:
            loop.set_debug(debug)

        task = loop.create_task(coro)
        for signame in ["SIGHUP", "SIGINT", "SIGTERM"]:
            loop.add_signal_handler(getattr(signal, signame), signal_handler, task)

        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        if hasattr(loop, "shutdown_default_executor"):
            loop.run_until_complete(loop.shutdown_default_executor())
        asyncio.set_event_loop(None)
        loop.close()


async def run_concurrently(*coros):
    """Run coros concurrently and cancel others on error.

    Like `asyncio.gather()`, but if one task raises an exception, all other
    tasks are canceled.
    """

    tasks = [asyncio.create_task(coro) for coro in coros]
    try:
        return await asyncio.gather(*tasks)
    finally:
        await cancel_tasks(tasks)


@contextlib.asynccontextmanager
async def background_task(coro):
    """Runs the coro until leaving the context manager.

    The coro task is canceled when leaving the context."""
    task = asyncio.create_task(coro)
    try:
        yield task
    finally:
        await cancel_tasks([task])


async def cancel_tasks(tasks):
    """Cancel tasks and wait until all are canceled"""

    for task in tasks:
        if not task.done():
            task.cancel()

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.warning("Ignoring error in canceled task: %s", result)


@contextlib.asynccontextmanager
async def on_error(callback, /, *args, **kwargs):
    """Calls a cleanup callback, if an exception is raised within the
    context manager.
    """
    async with contextlib.AsyncExitStack() as stack:
        stack.push_async_callback(callback, *args, **kwargs)
        yield
        stack.pop_all()


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
    but all currently running connection handler callbacks are also
    canceled.

    It also wraps the connection handler to catch and log every exception
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


class ContextStack:
    """Mix-in class to simplify implementing a context manager

    Child classes can implement the _context_stack() function, instead of
    __aenter__() and __aexit__(). _context_stack() is called when entering the
    context. It needs to to build up an AsyncExitStack(), that is passed as an
    argument, which is then cleaned up when exiting the context.
    """

    async def _context_stack(self, stack):
        raise NotImplementedError()

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            await self._context_stack(stack)
            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)


@contextlib.asynccontextmanager
async def flock(f):
    """File lock as a context manager"""

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, fcntl.flock, f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
