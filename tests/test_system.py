import pytest


@pytest.fixture(scope="module")
async def farm(vms):
    async with (
        vms.hub.ssh_task("not-my-board hub", "hub", wait_ready=True),
        vms.exporter.ssh_task(
            "not-my-board export http://hub.local:2092 ./src/tests/system_test/place1.toml",
            "export1",
            wait_ready=True,
        ),
        vms.exporter.ssh_task(
            "not-my-board export http://hub.local:2092 ./src/tests/system_test/place2.toml",
            "export2",
            wait_ready=True,
        ),
        vms.client.ssh_task_root(
            "not-my-board agent http://hub.local:2092", "agent", wait_ready=True
        ),
    ):
        yield vms


async def test_reserve_by_name(farm):
    await farm.client.ssh(
        "not-my-board reserve ./src/tests/system_test/nothing.toml@place1"
    )

    result = await farm.client.ssh("not-my-board list --no-header")
    assert result.stdout.split()[0] == "nothing@place1"


async def test_unique_place_name(farm):
    result = await farm.exporter.ssh(
        "not-my-board export http://hub.local:2092 ./src/tests/system_test/duplicate/place1.toml 2>&1",
        check=False,
    )
    assert result.returncode != 0
    assert 'Place with name "place1" already registered' in result.stdout
