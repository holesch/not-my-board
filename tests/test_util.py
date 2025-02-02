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


@pytest.mark.parametrize(
    ("time_string", "seconds"),
    [
        ("10h", 10 * 60 * 60),
        ("600", 600),
        ("10m", 10 * 60),
        ("1h30m", 1 * 60 * 60 + 30 * 60),
        ("1h30m10s", 1 * 60 * 60 + 30 * 60 + 10),
        ("2w3d4h", 2 * 7 * 24 * 60 * 60 + 3 * 24 * 60 * 60 + 4 * 60 * 60),
    ],
)
async def test_parse_time(time_string, seconds):
    assert util.parse_time(time_string) == seconds


@pytest.mark.parametrize(
    ("time_string"),
    [
        "1s10h",
        "abc",
        "h",
        "10H",
        "1a",
    ],
)
async def test_parse_time_invalid(time_string):
    with pytest.raises(RuntimeError) as execinfo:
        util.parse_time(time_string)
    assert "Invalid time format" in str(execinfo.value)


async def test_parse_empty_time_string():
    with pytest.raises(RuntimeError) as execinfo:
        util.parse_time("")
    assert "Time is an empty string" in str(execinfo.value)


async def test_server_shutdown():
    ready_event = asyncio.Event()

    async def server():
        async def handle_client(reader, writer):
            data = await reader.read(1024)
            writer.write(data)
            await writer.drain()

        async with util.Server(handle_client, host="localhost", port=12345) as server:
            ready_event.set()
            await server.serve_forever()

    async with util.background_task(server()) as server:
        # wait for server to come up
        await ready_event.wait()

        async with util.connect("localhost", 8888) as (reader, writer):
            # While a connection is open, cancel the server task. This test
            # checks, that closing the server also cancels every connection, so
            # that the shutdown doesn't block forever.
            await util.cancel_tasks([server])
