import asyncio
import contextlib

import pytest

import not_my_board._hub as hubmodule
import not_my_board._jsonrpc as jsonrpc
import not_my_board._util as util

DEFAULT_EXPORTER_IP = "3.1.1.1"
DEFAULT_AGENT_IP = "6.1.1.1"


@pytest.fixture(scope="function")
def hub():
    yield hubmodule.Hub()


async def test_no_places_on_startup(hub):
    places = await hub.get_places()
    assert places["places"] == []


class FakeExporter:
    def __init__(self, register_event):
        self._register_event = register_event

    async def io_loop(self):
        # wait forever
        await asyncio.Event().wait()

    async def get_place(self):
        self._register_event.set()
        return {
            "port": 1234,
            "parts": [
                {
                    "compatible": [
                        "test-board",
                    ],
                    "tcp": {
                        "test-if": {
                            "host": "localhost",
                            "port": 8080,
                        },
                    },
                },
            ],
        }

    async def set_allowed_ips(self, ips):
        self._allowed_ips = ips

    @property
    def allowed_ips(self):
        return self._allowed_ips


# pylint: disable=redefined-outer-name
@contextlib.asynccontextmanager
async def register_exporter(hub):
    exporter_ip = DEFAULT_EXPORTER_IP
    register_event = asyncio.Event()
    fake_exporter = FakeExporter(register_event)
    coro = hub.exporter_communicate(exporter_ip, fake_exporter)
    async with util.background_task(coro) as exporter_task:
        async with asyncio.timeout(2):
            await register_event.wait()
        yield fake_exporter, exporter_task


async def test_register_exporter(hub):
    async with register_exporter(hub):
        places = await hub.get_places()
        assert len(places["places"]) == 1

    places = await hub.get_places()
    assert len(places["places"]) == 0


def fake_rpc_pair():
    proxy_to_server = asyncio.Queue()
    server_to_proxy = asyncio.Queue()

    async def receive_iter(queue):
        while True:
            data = await queue.get()
            yield data
            queue.task_done()

    server = jsonrpc.Server(server_to_proxy.put, receive_iter(proxy_to_server))
    proxy = jsonrpc.Proxy(proxy_to_server.put, receive_iter(server_to_proxy))
    return server, proxy


# pylint: disable=redefined-outer-name
@contextlib.asynccontextmanager
async def register_agent(hub):
    agent_ip = DEFAULT_AGENT_IP
    server, proxy = fake_rpc_pair()
    coro = hub.agent_communicate(agent_ip, server)
    async with util.background_task(coro):
        async with util.background_task(proxy.io_loop()):
            yield proxy


async def test_reserve_place(hub):
    async with register_exporter(hub) as (exporter, _):
        async with register_agent(hub) as agent:
            places = await hub.get_places()
            candidate_ids = [places["places"][0]["id"]]
            reserved_id = await agent.reserve(candidate_ids)
            assert reserved_id == candidate_ids[0]
            assert exporter.allowed_ips == [DEFAULT_AGENT_IP]


async def test_reserve_non_existent(hub):
    async with register_agent(hub) as agent:
        candidate_ids = [42]
        with pytest.raises(jsonrpc.RemoteError) as execinfo:
            await agent.reserve(candidate_ids)
        assert "None of the candidates exist anymore" in str(execinfo.value)


async def test_reserve_queue(hub):
    async with register_exporter(hub):
        async with register_agent(hub) as agent:
            places = await hub.get_places()
            candidate_ids = [places["places"][0]["id"]]
            reserved_id = await agent.reserve(candidate_ids)

            # try to reserve same place again
            coro = agent.reserve(candidate_ids)
            async with util.background_task(coro) as reserve_task:
                await asyncio.sleep(0.001)
                # request should be in queue now
                assert not reserve_task.done()

                # when the first reservation is returned ...
                await agent.return_reservation(reserved_id)
                # ... then the second one can be fulfilled
                assert await reserve_task == reserved_id


async def test_all_places_disappear_while_trying_to_reserve(hub):
    async with register_exporter(hub) as (_, exporter_task):
        async with register_agent(hub) as agent:
            places = await hub.get_places()
            candidate_ids = [places["places"][0]["id"]]
            await agent.reserve(candidate_ids)

            # try to reserve same place again
            coro = agent.reserve(candidate_ids)
            async with util.background_task(coro) as reserve_task:
                await asyncio.sleep(0.001)
                # request should be in queue now

                # when the exporter disappears ...
                await util.cancel_tasks([exporter_task])
                # ... then the queued reservation is canceled
                with pytest.raises(Exception) as execinfo:
                    await reserve_task
                assert "All candidate places are gone" in str(execinfo.value)


async def test_one_place_disappears_while_trying_to_reserve(hub):
    async with register_exporter(hub):
        async with register_exporter(hub) as (_, exporter_task):
            async with register_agent(hub) as agent:
                places = await hub.get_places()
                # reserve both places
                candidate_ids = [p["id"] for p in places["places"]]
                await agent.reserve(candidate_ids)
                await agent.reserve(candidate_ids)

                # try to reserve both places again
                coro = agent.reserve(candidate_ids)
                async with util.background_task(coro) as reserve_task:
                    await asyncio.sleep(0.001)
                    # request should be in queue now
                    assert not reserve_task.done()

                    # when one exporter disappears ...
                    await util.cancel_tasks([exporter_task])
                    # ... then the queued reservation is still active
                    assert not reserve_task.done()


async def test_return_non_candidate(hub):
    async with register_exporter(hub):
        async with register_exporter(hub):
            async with register_agent(hub) as agent:
                places = await hub.get_places()
                # reserve both places
                candidate_ids = [p["id"] for p in places["places"]]
                await agent.reserve(candidate_ids)
                await agent.reserve(candidate_ids)

                # try to reserve place #1 again
                coro = agent.reserve(candidate_ids[:1])
                async with util.background_task(coro) as reserve_task:
                    await asyncio.sleep(0.001)
                    # request should be in queue now

                    # when place #2 is returned ...
                    await agent.return_reservation(candidate_ids[1])
                    # ... then the queued reservation is still active
                    assert not reserve_task.done()