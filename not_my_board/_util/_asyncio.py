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


def background_task(coro):
    """Runs coro as a background task until leaving the context manager.

    If the background task fails while the context manager is active, then the
    foreground task is canceled and the context manager raises the exception of
    the background task.

    If the context manager exits while the background task is still running,
    then the background task is canceled."""

    return _BackgroundTask(coro)


class _BackgroundTask:
    def __init__(self, coro):
        self._coro = coro
        self._bg_exception = None

    async def __aenter__(self):
        self._bg_task = asyncio.create_task(self._coro)
        self._bg_task.add_done_callback(self._on_bg_task_done)
        self._fg_task = asyncio.current_task()
        self._num_cancel_requests = self._get_num_cancel_requests()
        return self._bg_task

    async def __aexit__(self, exc_type, exc, tb):
        self._bg_task.remove_done_callback(self._on_bg_task_done)
        if self._bg_exception:
            if (
                self._uncancel() <= self._num_cancel_requests
                and exc_type is asyncio.CancelledError
            ):
                # foreground task was only canceled by this class, raise
                # real error
                raise self._bg_exception from exc
        else:
            await cancel_tasks([self._bg_task])

    def _on_bg_task_done(self, task):
        if not task.cancelled():
            self._bg_exception = task.exception()
            if self._bg_exception:
                self._fg_task.cancel()

    def _get_num_cancel_requests(self):
        # remove, if Python version < 3.11 is no longer supported
        if hasattr(self._fg_task, "cancelling"):
            return self._fg_task.cancelling()
        else:
            return 0

    def _uncancel(self):
        # remove, if Python version < 3.11 is no longer supported
        if hasattr(self._fg_task, "uncancel"):
            return self._fg_task.uncancel()
        else:
            return 0


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
        # close listening socket, don't accept new connections
        self._server.close()
        # cancel active connection handlers
        await cancel_tasks(self._tasks.copy())
        # wait for all connections to close
        await self._server.wait_closed()

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
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _start(self):
        return await asyncio.start_server(self._on_connect, *self._args, **self._kwargs)

    async def serve_forever(self):
        # Since Python 3.12 self._server.serve_forever() hangs when canceled
        # until all connections are closed. Just block on an event until
        # canceled.
        await asyncio.Event().wait()


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
        raise NotImplementedError

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

    try:
        await run_in_thread(fcntl.flock, f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)


async def run_in_thread(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func, *args)
