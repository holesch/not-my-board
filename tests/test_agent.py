import asyncio
import contextlib

import pytest

import not_my_board._agent as agentmodule
import not_my_board._models as models
import not_my_board._util as util

HUB_URL = "http://fake.farm"
PLACE_1 = models.Place(
    id=1289,
    host="3.1.1.1",
    port=2000,
    parts=[
        models.ExportedPart(
            compatible=["fake-board"],
            usb={
                "usb0": models.UsbExportDesc(usbid="1-3"),
            },
            tcp={
                "ssh": models.TcpExportDesc(host="10.0.0.5", port=22),
            },
        )
    ],
)

PLACE_LOCALHOST = models.Place(
    id=338,
    host="127.0.0.1",
    port=2000,
    parts=[
        models.ExportedPart(
            compatible=["fake-board"],
            usb={
                "usb0": models.UsbExportDesc(usbid="1-3"),
            },
            tcp={
                "ssh": models.TcpExportDesc(host="10.0.0.5", port=22),
            },
        )
    ],
)

PLACE_COMPLEX = models.Place(
    id=4311,
    host="3.1.1.1",
    port=2001,
    parts=[
        models.ExportedPart(
            compatible=["fake-board"],
            usb={
                "usb0": models.UsbExportDesc(usbid="1-3"),
            },
            tcp={
                "ssh": models.TcpExportDesc(host="10.0.0.5", port=22),
            },
        ),
        models.ExportedPart(
            compatible=["fake-board"],
            tcp={
                "ssh": models.TcpExportDesc(host="10.0.0.5", port=22),
            },
        ),
    ],
)

IMPORT_DESC_1 = models.ImportDesc(
    name="fake",
    parts={
        "fake-board": models.ImportedPart(
            compatible=["fake-board"],
            usb={
                "usb0": models.UsbImportDesc(port_num=1),
            },
            tcp={
                "ssh": models.TcpImportDesc(local_port=2222),
            },
        )
    },
)

IMPORT_DESC_NOT_FOUND = models.ImportDesc(
    name="fake",
    parts={
        "fake-board": models.ImportedPart(
            compatible=["does-not-exist"],
        )
    },
)

IMPORT_DESC_COMPLEX = models.ImportDesc(
    name="fake",
    parts={
        "fake-board-ssh": models.ImportedPart(
            compatible=["fake-board"],
            tcp={
                "ssh": models.TcpImportDesc(local_port=2222),
            },
        ),
        "fake-board-usb": models.ImportedPart(
            compatible=["fake-board"],
            usb={
                "usb0": models.UsbImportDesc(port_num=1),
            },
        ),
    },
)

IMPORT_DESC_COMPLEX_NOT_FOUND = models.ImportDesc(
    name="fake",
    parts={
        "fake-board-ssh": models.ImportedPart(
            compatible=["fake-board"],
            tcp={
                "ssh": models.TcpImportDesc(local_port=2222),
            },
        ),
        "fake-board-usb": models.ImportedPart(
            compatible=["fake-board"],
            usb={
                "usb0": models.UsbImportDesc(port_num=1),
            },
        ),
        "fake-board-any": models.ImportedPart(
            compatible=["fake-board"],
        ),
    },
)


class FakeHub:
    def __init__(self):
        self.reserve_request = None
        self.reserved = set()
        self.reserve_continue = asyncio.Event()
        self.reserve_pending = asyncio.Event()
        self.reserve_continue.set()

    async def reserve(self, candidate_ids):
        self.reserve_pending.set()
        try:
            await self.reserve_continue.wait()
        finally:
            self.reserve_pending.clear()

        self.reserve_request = candidate_ids
        self.reserved.add(candidate_ids[0])
        return candidate_ids[0]

    async def return_reservation(self, place_id):
        self.reserved.remove(place_id)


class NopServer:
    async def serve_forever(self):
        await asyncio.Event().wait()


class FakeAgentIO:
    def __init__(self):
        self.places = []
        self.attached = {}
        self.detach_event = {}
        self.port_forwards = {}

    @contextlib.asynccontextmanager
    async def hub_rpc(self):
        self.hub = FakeHub()
        yield self.hub

    @contextlib.asynccontextmanager
    async def unix_server(self, api_obj):
        self.agent_api = api_obj
        yield NopServer()

    async def get_places(self):
        return self.places

    @staticmethod
    async def usbip_refresh_status():
        pass

    def usbip_is_attached(self, vhci_port):
        return vhci_port in self.attached

    async def usbip_attach(self, proxy, target, port_num, usbid):
        if port_num in self.detach_event:
            await self.detach_event[port_num].wait()
        self.attached[port_num] = (proxy, target, usbid)
        self.detach_event[port_num] = asyncio.Event()
        return port_num

    def usbip_detach(self, vhci_port):
        if vhci_port in self.attached:
            del self.attached[vhci_port]
            self.detach_event[vhci_port].set()
            del self.detach_event[vhci_port]

    async def port_forward(self, ready_event, proxy, target, local_port):
        self.port_forwards[local_port] = (proxy, target)
        ready_event.set()
        try:
            await asyncio.Event().wait()
        finally:
            del self.port_forwards[local_port]


@pytest.fixture(scope="function")
async def agent_io():
    io = FakeAgentIO()
    async with agentmodule.Agent(HUB_URL, io) as agent:
        async with util.background_task(agent.serve_forever()):
            yield io


async def test_idle_list(agent_io):
    list_ = await agent_io.agent_api.list()
    assert list_ == []


async def test_idle_status(agent_io):
    status = await agent_io.agent_api.status()
    assert status == []


async def test_reserve(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    assert agent_io.hub.reserve_request == [PLACE_1.id]
    assert agent_io.hub.reserved == {PLACE_1.id}


async def test_attach(agent_io):
    agent_io.places = [PLACE_1]

    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    port_num = IMPORT_DESC_1.parts["fake-board"].usb["usb0"].port_num
    proxy = (PLACE_1.host, PLACE_1.port)
    usbip_target = ("usb.not-my-board.localhost", 3240)
    usbip = PLACE_1.parts[0].usb["usb0"].usbid
    assert agent_io.attached == {port_num: (proxy, usbip_target, usbip)}
    local_port = IMPORT_DESC_1.parts["fake-board"].tcp["ssh"].local_port
    tcp_target = tuple(
        getattr(PLACE_1.parts[0].tcp["ssh"], k) for k in ("host", "port")
    )
    assert agent_io.port_forwards == {local_port: (proxy, tcp_target)}


async def test_list_place_1(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    list_ = await agent_io.agent_api.list()
    assert list_ == [{"place": "fake", "attached": False}]


async def test_list_place_1_attached(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    list_ = await agent_io.agent_api.list()
    assert list_ == [{"place": "fake", "attached": True}]


async def test_status_place_1(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    status = await agent_io.agent_api.status()
    assert len(status) == 2
    usb0_status = {
        "place": "fake",
        "part": "fake-board",
        "interface": "usb0",
        "type": "USB",
        "attached": False,
    }
    ssh_status = {
        "place": "fake",
        "part": "fake-board",
        "interface": "ssh",
        "type": "TCP",
        "attached": False,
    }
    assert usb0_status in status
    assert ssh_status in status


async def test_status_place_1_attached(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    status = await agent_io.agent_api.status()
    assert len(status) == 2
    assert status[0]["attached"] is True
    assert status[1]["attached"] is True


async def test_reserve_twice(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    assert "is already reserved" in str(execinfo.value)


async def test_return_reservation(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.return_reservation(IMPORT_DESC_1.name)
    assert not agent_io.hub.reserved


async def test_detach(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    await agent_io.agent_api.detach(IMPORT_DESC_1.name)
    assert not agent_io.attached
    assert not agent_io.port_forwards

    status = await agent_io.agent_api.status()
    assert len(status) == 2
    assert not status[0]["attached"]
    assert not status[1]["attached"]


async def test_return_reservation_while_attached(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.return_reservation(IMPORT_DESC_1.name)
    assert "is still attached" in str(execinfo.value)


async def test_force_return_reservation(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    await agent_io.agent_api.return_reservation(name=IMPORT_DESC_1.name, force=True)
    assert not agent_io.attached
    assert not agent_io.port_forwards
    assert not agent_io.hub.reserved


async def test_return_unreserved_place(agent_io):
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.return_reservation(IMPORT_DESC_1.name)
    assert "is not reserved" in str(execinfo.value)


async def test_no_match_found(agent_io):
    agent_io.places = [PLACE_1]
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.reserve(IMPORT_DESC_NOT_FOUND.dict())
    assert "No matching place found" in str(execinfo.value)


async def test_localhost_exporter(agent_io):
    """This happens if the Hub and an Exporter run on the same host"""
    agent_io.places = [PLACE_LOCALHOST]

    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    port_num = IMPORT_DESC_1.parts["fake-board"].usb["usb0"].port_num
    # place host needs to be replaced with hub host
    proxy = ("fake.farm", PLACE_1.port)
    assert agent_io.attached[port_num][0] == proxy


async def test_attach_twice(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    assert "is already attached" in str(execinfo.value)


async def test_detach_twice(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    await agent_io.agent_api.attach(IMPORT_DESC_1.name)
    await agent_io.agent_api.detach(IMPORT_DESC_1.name)
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.detach(IMPORT_DESC_1.name)
    assert "is not attached" in str(execinfo.value)


async def test_complex_match(agent_io):
    agent_io.places = [PLACE_COMPLEX]
    await agent_io.agent_api.reserve(IMPORT_DESC_COMPLEX.dict())
    assert await agent_io.agent_api.list()


async def test_complex_match_not_found(agent_io):
    agent_io.places = [PLACE_COMPLEX]
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.reserve(IMPORT_DESC_COMPLEX_NOT_FOUND.dict())
    assert "No matching place found" in str(execinfo.value)


async def test_reserve_twice_concurrently(agent_io):
    agent_io.places = [PLACE_1]

    # make the reserve call blocking
    agent_io.hub.reserve_continue.clear()

    # start two reserve tasks in parallel
    coro_1 = agent_io.agent_api.reserve(IMPORT_DESC_1.dict())
    coro_2 = agent_io.agent_api.reserve(IMPORT_DESC_1.dict())

    with pytest.raises(RuntimeError) as execinfo:
        async with util.background_task(coro_1) as task_1:
            async with util.background_task(coro_2) as task_2:
                # wait until one blocks
                await agent_io.hub.reserve_pending.wait()

                # the other one should block until the first one finishes
                assert not task_1.done()
                assert not task_2.done()

                # unblock reserve call
                agent_io.hub.reserve_continue.set()

                await asyncio.sleep(0.5)

    # now one should finish successfully and the other one should fail
    results = await asyncio.gather(task_1, task_2, return_exceptions=True)

    assert None in results
    assert "is already reserved" in str(execinfo.value)

    assert len(await agent_io.agent_api.list()) == 1
