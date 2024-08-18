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

IMPORT_DESC_1 = """
    [parts.fake-board]
    compatible = [ "fake-board" ]
    usb.usb0 = { port_num = 1 }
    tcp.ssh = { local_port = 2222 }
"""

IMPORT_DESC_NOT_FOUND = """
    [parts.fake-board]
    compatible = [ "does-not-exist" ]
"""

IMPORT_DESC_COMPLEX = """
    [parts.fake-board-ssh]
    compatible = [ "fake-board" ]
    tcp.ssh = { local_port = 2222 }
    [parts.fake-board-usb]
    compatible = [ "fake-board" ]
    usb.usb0 = { port_num = 1 }
"""

IMPORT_DESC_COMPLEX_NOT_FOUND = """
    [parts.fake-board-ssh]
    compatible = [ "fake-board" ]
    tcp.ssh = { local_port = 2222 }
    [parts.fake-board-usb]
    compatible = [ "fake-board" ]
    usb.usb0 = { port_num = 1 }
    [parts.fake-board-any]
    compatible = [ "fake-board" ]
"""


class FakeHub:
    def __init__(self):
        self.reserve_request = None
        self.reserved = set()
        self.reserve_continue = asyncio.Event()
        self.reserve_pending = asyncio.Event()
        self.reserve_continue.set()

    def set_api_object(self, _):
        pass

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
    def usbip_refresh_status():
        pass

    def usbip_is_attached(self, vhci_port):
        return vhci_port in self.attached

    @staticmethod
    def usbip_port_num_to_busid(_):
        return ["2-1", "3-1"]

    @staticmethod
    def usbip_vhci_port_to_busid(_):
        return "2-1"

    async def usbip_attach(self, proxy, target, port_num, usbid):
        if port_num in self.detach_event:
            await self.detach_event[port_num].wait()
        self.attached[port_num] = (proxy, target, usbid)
        self.detach_event[port_num] = asyncio.Event()
        return port_num

    async def usbip_detach(self, vhci_port):
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


@pytest.fixture
async def agent_io():
    io = FakeAgentIO()
    async with agentmodule.Agent(HUB_URL, io, None) as agent:
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
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    assert agent_io.hub.reserve_request == [PLACE_1.id]
    assert agent_io.hub.reserved == {PLACE_1.id}


async def test_attach(agent_io):
    agent_io.places = [PLACE_1]

    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    port_num = 1
    proxy = (PLACE_1.host, PLACE_1.port)
    usbip_target = ("usb.not-my-board.localhost", 3240)
    usbid = PLACE_1.parts[0].usb["usb0"].usbid
    assert agent_io.attached == {port_num: (proxy, usbip_target, usbid)}
    local_port = 2222
    tcp_target = tuple(
        getattr(PLACE_1.parts[0].tcp["ssh"], k) for k in ("host", "port")
    )
    assert agent_io.port_forwards == {local_port: (proxy, tcp_target)}


async def test_list_place_1(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    list_ = await agent_io.agent_api.list()
    assert list_ == [{"place": "fake", "attached": False}]


async def test_list_place_1_attached(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    list_ = await agent_io.agent_api.list()
    assert list_ == [{"place": "fake", "attached": True}]


async def test_status_place_1(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    status = await agent_io.agent_api.status()
    assert len(status) == 2
    usb0_status = {
        "place": "fake",
        "part": "fake-board",
        "interface": "usb0",
        "type": "USB",
        "attached": False,
        "port": "2-1/3-1",
    }
    ssh_status = {
        "place": "fake",
        "part": "fake-board",
        "interface": "ssh",
        "type": "TCP",
        "attached": False,
        "port": "2222",
    }
    assert usb0_status in status
    assert ssh_status in status


async def test_status_place_1_attached(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    status = await agent_io.agent_api.status()
    assert len(status) == 2
    assert status[0]["attached"] is True
    assert status[1]["attached"] is True
    assert "2-1" in [s["port"] for s in status]


async def test_reserve_twice(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    assert "is already reserved" in str(execinfo.value)


async def test_return_reservation(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.return_reservation("fake")
    assert not agent_io.hub.reserved


async def test_detach(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    await agent_io.agent_api.detach("fake")
    assert not agent_io.attached
    assert not agent_io.port_forwards

    status = await agent_io.agent_api.status()
    assert len(status) == 2
    assert not status[0]["attached"]
    assert not status[1]["attached"]


async def test_return_reservation_while_attached(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.return_reservation("fake")
    assert "is still attached" in str(execinfo.value)


async def test_force_return_reservation(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    await agent_io.agent_api.return_reservation(name="fake", force=True)
    assert not agent_io.attached
    assert not agent_io.port_forwards
    assert not agent_io.hub.reserved


async def test_return_unreserved_place(agent_io):
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.return_reservation("fake")
    assert "is not reserved" in str(execinfo.value)


async def test_no_match_found(agent_io):
    agent_io.places = [PLACE_1]
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.reserve("fake", IMPORT_DESC_NOT_FOUND)
    assert "No matching place found" in str(execinfo.value)


async def test_localhost_exporter(agent_io):
    """This happens if the Hub and an Exporter run on the same host"""
    agent_io.places = [PLACE_LOCALHOST]

    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    port_num = 1
    # place host needs to be replaced with hub host
    proxy = ("fake.farm", PLACE_1.port)
    assert agent_io.attached[port_num][0] == proxy


async def test_attach_twice(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.attach("fake")
    assert "is already attached" in str(execinfo.value)


async def test_detach_twice(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    await agent_io.agent_api.detach("fake")
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.detach("fake")
    assert "is not attached" in str(execinfo.value)


async def test_complex_match(agent_io):
    agent_io.places = [PLACE_COMPLEX]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_COMPLEX)
    assert await agent_io.agent_api.list()


async def test_complex_match_not_found(agent_io):
    agent_io.places = [PLACE_COMPLEX]
    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.reserve("fake", IMPORT_DESC_COMPLEX_NOT_FOUND)
    assert "No matching place found" in str(execinfo.value)


async def test_reserve_twice_concurrently(agent_io):
    agent_io.places = [PLACE_1]

    # make the reserve call blocking
    agent_io.hub.reserve_continue.clear()

    # start two reserve tasks in parallel
    coro_1 = agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    coro_2 = agent_io.agent_api.reserve("fake", IMPORT_DESC_1)

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


async def test_get_import_description(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    import_description_toml = await agent_io.agent_api.get_import_description("fake")
    assert import_description_toml == IMPORT_DESC_1


async def test_update_import_description(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    new_import_description = """
        [parts.fake-board]
        compatible = [ "fake-board" ]
        usb.usb0 = { port_num = 7 }
        tcp.ssh = { local_port = 2222 }
    """
    await agent_io.agent_api.update_import_description("fake", new_import_description)
    await agent_io.agent_api.attach("fake")
    usbid = PLACE_1.parts[0].usb["usb0"].usbid
    assert agent_io.attached[7][2] == usbid


async def test_update_import_description_attached(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    new_import_description = """
        [parts.fake-board]
        compatible = [ "fake-board" ]
        usb.usb0 = { port_num = 7 }
        tcp.ssh = { local_port = 2222 }
    """
    await agent_io.agent_api.update_import_description("fake", new_import_description)
    usbid = PLACE_1.parts[0].usb["usb0"].usbid
    assert agent_io.attached[7][2] == usbid


async def test_update_import_description_not_matching(agent_io):
    agent_io.places = [PLACE_1]
    await agent_io.agent_api.reserve("fake", IMPORT_DESC_1)
    await agent_io.agent_api.attach("fake")
    new_import_description = """
        [parts.fake-board]
        compatible = [ "does-not-match" ]
        usb.usb0 = { port_num = 7 }
        tcp.ssh = { local_port = 2222 }
    """

    with pytest.raises(RuntimeError) as execinfo:
        await agent_io.agent_api.update_import_description(
            "fake", new_import_description
        )

    assert "New import description doesn't match with place" in str(execinfo.value)
