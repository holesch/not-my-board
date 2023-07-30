import asyncio

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Redefine event_loop fixture with session scope"""

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
