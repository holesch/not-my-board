import asyncio

import pytest

import not_my_board._util as util

from .util import ClientVM, ExporterVM, HubVM, VMs, wait_for_ports


@pytest.fixture(scope="session")
def event_loop():
    """Redefine event_loop fixture with session scope"""

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def vms():
    async with HubVM() as hub:
        await wait_for_ports(5001, 5002)
        async with ExporterVM() as exporter:
            async with ClientVM() as client:
                await util.run_concurrently(
                    hub.configure(),
                    exporter.configure(),
                    client.configure(),
                )
                await exporter.usb_attach()
                yield VMs(hub, exporter, client)
