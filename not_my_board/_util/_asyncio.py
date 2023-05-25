import asyncio


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
