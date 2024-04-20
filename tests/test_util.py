import asyncio

import pytest

import not_my_board._util as util


async def test_background_task():
    async with util.background_task(blocking_task()) as task:
        assert not task.done()
    assert task.cancelled()


async def test_background_task_failed():
    with pytest.raises(RuntimeError) as execinfo:
        async with util.background_task(failing_task()):
            await asyncio.sleep(3)
    assert "Dummy Error" in str(execinfo.value)


async def blocking_task():
    await asyncio.Event().wait()


async def failing_task():
    raise RuntimeError("Dummy Error")
