import asyncio
import functools
import traceback


async def run_concurrently(*coros):
    tasks = [asyncio.create_task(coro) for coro in coros]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()

        for task in tasks:
            if not task.done():
                try:
                    await task
                except asyncio.CancelledError:
                    pass


def connection_handler(func):
    """Decorator for asyncio connection handlers

    Catches and logs every exception raised in the handler and closes the
    connection, when the handler function returns.
    """

    @functools.wraps(func)
    async def wrapper(self, reader, writer):
        try:
            await func(self, reader, writer)
        except Exception:
            traceback.print_exc()
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    return wrapper
