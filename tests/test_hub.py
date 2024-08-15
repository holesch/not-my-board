import asyncio
import contextlib

import pytest

import not_my_board._hub as hubmodule
import not_my_board._jsonrpc as jsonrpc
import not_my_board._util as util

from .util import fake_rpc_pair

DEFAULT_EXPORTER_IP = "3.1.1.1"
DEFAULT_AGENT_IP = "6.1.1.1"


@pytest.fixture
def hub():
    return hubmodule.Hub()


async def test_no_places_on_startup(hub):
    places = await hub.get_places()
    assert places["places"] == []


class FakeExporter:
    def __init__(self, rpc):
        rpc.set_api_object(self)
        self._rpc = rpc

    async def communicate_forever(self):
        await self._rpc.communicate_forever()

    async def register_place(self):
        place = {
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
        await self._rpc.register_place(place)

    async def set_allowed_ips(self, ips):
        self._allowed_ips = ips

    @property
    def allowed_ips(self):
        return self._allowed_ips


@contextlib.asynccontextmanager
async def register_exporter(hub, ip=DEFAULT_EXPORTER_IP):
    rpc1, rpc2 = fake_rpc_pair()
    fake_exporter = FakeExporter(rpc1)
    hub_coro = hub.communicate(ip, rpc2)
    exporter_coro = fake_exporter.communicate_forever()
    async with util.background_task(hub_coro) as hub_connection:
        async with util.background_task(exporter_coro):
            await fake_exporter.register_place()
            yield fake_exporter, hub_connection


async def test_register_exporter(hub):
    async with register_exporter(hub):
        places = await hub.get_places()
        assert len(places["places"]) == 1

    places = await hub.get_places()
    assert len(places["places"]) == 0


@contextlib.asynccontextmanager
async def register_agent(hub, ip=DEFAULT_AGENT_IP):
    rpc1, rpc2 = fake_rpc_pair()
    coro = hub.communicate(ip, rpc2)
    async with util.background_task(coro):
        async with util.background_task(rpc1.communicate_forever()):
            yield rpc1


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

            with pytest.raises(jsonrpc.RemoteError) as execinfo:
                # try to reserve same place again
                coro = agent.reserve(candidate_ids)
                async with util.background_task(coro):
                    await asyncio.sleep(0.001)
                    # request should be in queue now

                    # when the exporter disappears ...
                    await util.cancel_tasks([exporter_task])
                    await asyncio.sleep(0.5)
            # ... then the queued reservation is canceled
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


async def test_mapped_ip_exporter(hub):
    async with register_exporter(hub, ip="::FFFF:10.0.0.8"):
        places = await hub.get_places()
        assert places["places"][0]["host"] == "10.0.0.8"


async def test_mapped_ip_agent(hub):
    async with register_exporter(hub) as (exporter, _):
        async with register_agent(hub, ip="::FFFF:10.0.0.9") as agent:
            places = await hub.get_places()
            candidate_ids = [places["places"][0]["id"]]
            await agent.reserve(candidate_ids)
            assert exporter.allowed_ips == ["10.0.0.9"]
