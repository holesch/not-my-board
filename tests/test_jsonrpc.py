import asyncio
import collections
import contextlib
import io
import json

import pytest

import not_my_board._jsonrpc as jsonrpc
import not_my_board._util as util


class FakeApi:
    def __init__(self):
        self.event = asyncio.Event()

    async def get_42(self):
        return 42

    async def set_event(self):
        self.event.set()

    async def wait_for_event(self):
        await self.event.wait()

    async def _hidden(self):
        return "secret"

    async def error(self):
        raise RuntimeError("fake error")

    async def say_hello(self, name):
        return f"Hello, {name}!"

    @jsonrpc.hidden
    async def hidden(self):
        return "secret"


class FakeApi2:
    async def new_func(self):
        return "hi"


class FakeTransport:
    def __init__(self):
        self.receive_queue = asyncio.Queue()
        self.send_queue = asyncio.Queue()

    async def send_to_test(self, data):
        await self.receive_queue.put(data)

    async def receive_from_test(self):
        while True:
            data = await self.send_queue.get()
            yield data
            self.send_queue.task_done()

    async def send_to_jsonrpc(self, **data):
        data["jsonrpc"] = "2.0"
        raw = json.dumps(data).encode()
        await self.send_queue.put(raw)

    async def receive_from_jsonrpc(self):
        raw = await self.receive_queue.get()
        self.receive_queue.task_done()
        return json.loads(raw)

    def is_empty(self):
        return self.receive_queue.empty() and self.send_queue.empty()


Fakes = collections.namedtuple("Fakes", ["channel", "api", "transport"])


@pytest.fixture
async def fakes():
    transport = FakeTransport()
    api = FakeApi()
    channel = jsonrpc.Channel(
        transport.send_to_test, transport.receive_from_test(), api
    )
    async with channel:
        yield Fakes(channel, api, transport)


async def test_simple_call_execution(fakes):
    # send request
    await fakes.transport.send_to_jsonrpc(id=84, method="get_42")
    # check sent response
    message = await fakes.transport.receive_from_jsonrpc()
    assert message == {
        "jsonrpc": "2.0",
        "id": 84,
        "result": 42,
    }

    assert fakes.transport.is_empty()


async def test_execution_with_args(fakes):
    # send request
    await fakes.transport.send_to_jsonrpc(id=84, method="say_hello", params=["World"])
    # check sent response
    message = await fakes.transport.receive_from_jsonrpc()
    assert message["result"] == "Hello, World!"


async def test_execution_with_kwargs(fakes):
    # send request
    await fakes.transport.send_to_jsonrpc(
        id=84, method="say_hello", params={"name": "World"}
    )
    # check sent response
    message = await fakes.transport.receive_from_jsonrpc()
    assert message["result"] == "Hello, World!"


async def test_notification_execution(fakes):
    await fakes.transport.send_to_jsonrpc(method="set_event")

    # check if event was set
    async with util.timeout(1):
        await fakes.api.event.wait()

    # there should be no response
    assert fakes.transport.is_empty()


async def test_cancel_execution(fakes):
    await fakes.transport.send_to_jsonrpc(id=84, method="wait_for_event")

    # send cancellation request
    await fakes.transport.send_to_jsonrpc(id=85, method="rpc.cancel", params=[84])
    # check sent response
    message = await fakes.transport.receive_from_jsonrpc()
    assert message == {
        "jsonrpc": "2.0",
        "id": 85,
        "result": None,
    }

    assert fakes.transport.is_empty()


async def test_cancel_execution_invalid_id(fakes):
    # send cancellation request
    await fakes.transport.send_to_jsonrpc(id=85, method="rpc.cancel", params=[115])
    # response should still be OK
    message = await fakes.transport.receive_from_jsonrpc()
    assert message["result"] is None

    assert fakes.transport.is_empty()


async def test_prevent_hidden_function_execution(fakes):
    await fakes.transport.send_to_jsonrpc(id=84, method="_hidden")

    # call should fail
    message = await fakes.transport.receive_from_jsonrpc()
    assert message["error"]["message"] == "Method not found"


async def test_prevent_marked_hidden_function_execution(fakes):
    await fakes.transport.send_to_jsonrpc(id=84, method="hidden")

    # call should fail
    message = await fakes.transport.receive_from_jsonrpc()
    assert message["error"]["message"] == "Method not found"


async def test_return_exception(fakes):
    await fakes.transport.send_to_jsonrpc(id=84, method="error")
    # response should contain fake exception
    message = await fakes.transport.receive_from_jsonrpc()
    assert message["error"]["message"] == "fake error"


async def test_return_parse_error(fakes):
    await fakes.transport.send_to_jsonrpc(id=84, method=43)
    # response should contain parse error
    message = await fakes.transport.receive_from_jsonrpc()
    assert message["error"]["message"] == "Invalid Request"


async def test_replace_api_object(fakes):
    fakes.channel.set_api_object(FakeApi2())
    await fakes.transport.send_to_jsonrpc(id=84, method="new_func")
    # check sent response
    message = await fakes.transport.receive_from_jsonrpc()
    assert message["result"] == "hi"


async def test_simple_remote_call(fakes):
    async with util.background_task(fakes.channel.some_func()) as call:
        # check sent request
        message = await fakes.transport.receive_from_jsonrpc()
        assert message.keys() == {"jsonrpc", "method", "params", "id"}
        assert message["jsonrpc"] == "2.0"
        assert message["method"] == "some_func"
        assert not message["params"]

        # send response
        await fakes.transport.send_to_jsonrpc(id=message["id"], result=42)
        result = await call
        assert result == 42

        assert fakes.transport.is_empty()


async def test_remote_call_with_kwargs(fakes):
    async with util.background_task(fakes.channel.some_func(foo="bar")) as call:
        # check sent request
        message = await fakes.transport.receive_from_jsonrpc()
        assert message["params"] == {"foo": "bar"}

        # send response
        await fakes.transport.send_to_jsonrpc(id=message["id"], result=42)
        await call


async def test_send_notification(fakes):
    async with util.background_task(
        fakes.channel.some_notification(_notification=True)
    ) as call:
        # check sent notification
        message = await fakes.transport.receive_from_jsonrpc()
        assert message["method"] == "some_notification"
        assert "id" not in message
        await call


async def test_receive_error(fakes):
    with pytest.raises(jsonrpc.RemoteError) as execinfo:
        async with util.background_task(fakes.channel.some_func()):
            message = await fakes.transport.receive_from_jsonrpc()

            # send error response
            await fakes.transport.send_to_jsonrpc(
                id=message["id"],
                error={
                    "code": 123,
                    "message": "fake error",
                    "data": {
                        "traceback": (
                            "Traceback:\n"
                            '  File "fake", line 207, in fake_func\n'
                            "    raise FakeError()\n"
                            "FakeError: fake error\n"
                        ),
                    },
                },
            )
            await asyncio.sleep(0.5)

    # call should raise RemoteError
    assert "fake error" in str(execinfo.value)


async def test_send_cancellation(fakes):
    async with util.background_task(fakes.channel.some_func()) as call:
        # wait for request to be sent
        message1 = await fakes.transport.receive_from_jsonrpc()

        # cancel the request
        call.cancel()

        # expect cancellation message
        message2 = await fakes.transport.receive_from_jsonrpc()
        assert message2["method"] == "rpc.cancel"
        assert message2["params"] == [message1["id"]]

        # send cancellation response
        await fakes.transport.send_to_jsonrpc(id=message2["id"], result=None)

        with pytest.raises(asyncio.CancelledError):
            await call

        assert fakes.transport.is_empty()


async def test_cancel_cancellation(fakes):
    async with util.background_task(fakes.channel.some_func()) as call:
        # wait for request to be sent
        await fakes.transport.receive_from_jsonrpc()

        # cancel the request
        call.cancel()

        # expect cancellation message
        await fakes.transport.receive_from_jsonrpc()

        # cancel the cancellation
        call.cancel()

        with pytest.raises(asyncio.CancelledError):
            await call

        # there should be no second cancellation message
        assert fakes.transport.is_empty()


async def test_either_args_or_kwargs(fakes):
    with pytest.raises(RuntimeError) as execinfo:
        await fakes.channel.some_func(1, foo="bar")
    assert "Use either args or kwargs" in str(execinfo.value)


async def test_prevent_hidden_function_call(fakes):
    with pytest.raises(AttributeError) as execinfo:
        await fakes.channel._hidden()
    assert "invalid attribute" in str(execinfo.value)


async def test_fail_call_on_parse_error(fakes):
    with pytest.raises(jsonrpc.ProtocolError) as execinfo:
        async with util.background_task(fakes.channel.some_func()):
            # check sent request
            message = await fakes.transport.receive_from_jsonrpc()

            # send invalid error response
            await fakes.transport.send_to_jsonrpc(
                id=message["id"], error={"code": "not int"}
            )

            await asyncio.sleep(0.5)

    # call with matching ID should still fail
    assert '"error.code" must be an integer' in str(execinfo.value)


@contextlib.asynccontextmanager
async def capture_jsonrpc_error(fakes_):
    with contextlib.redirect_stderr(io.StringIO()) as f:
        yield f
        # send another request, to synchronize the handling of the first one
        await fakes_.transport.send_to_jsonrpc(method="set_event")
        await fakes_.api.event.wait()


async def test_parse_error_id(fakes):
    async with capture_jsonrpc_error(fakes) as f:
        await fakes.transport.send_to_jsonrpc(id=[])
    assert '"id" must be a string or number' in f.getvalue()


async def test_parse_error_method(fakes):
    async with capture_jsonrpc_error(fakes) as f:
        await fakes.transport.send_to_jsonrpc(method=33)
    assert '"method" must be a string' in f.getvalue()


async def test_parse_error_params(fakes):
    async with capture_jsonrpc_error(fakes) as f:
        await fakes.transport.send_to_jsonrpc(method="foo", params=33)
    assert '"params" must be a structured value' in f.getvalue()


async def test_parse_error_missing_id(fakes):
    async with capture_jsonrpc_error(fakes) as f:
        await fakes.transport.send_to_jsonrpc(result=None)
    assert '"id" is required' in f.getvalue()


async def test_parse_error_code(fakes):
    async with capture_jsonrpc_error(fakes) as f:
        await fakes.transport.send_to_jsonrpc(id=5, error={"code": "xxx"})
    assert '"error.code" must be an integer' in f.getvalue()


async def test_parse_error_message(fakes):
    async with capture_jsonrpc_error(fakes) as f:
        await fakes.transport.send_to_jsonrpc(id=5, error={"code": 4, "message": 3})
    assert '"error.message" must be a string' in f.getvalue()


async def test_log_error_for_notifications(fakes):
    async with capture_jsonrpc_error(fakes) as f:
        # send notification
        await fakes.transport.send_to_jsonrpc(method="error")
    assert "fake error" in f.getvalue()


async def test_pending_call_is_canceled_on_shutdown():
    transport = FakeTransport()
    channel = jsonrpc.Channel(transport.send_to_test, transport.receive_from_test())
    async with util.background_task(channel.communicate_forever()) as com_task:

        with pytest.raises(RuntimeError) as execinfo:
            async with util.background_task(channel.some_func()):
                # wait for sent message
                await transport.receive_from_jsonrpc()
                await util.cancel_tasks([com_task])
                await asyncio.sleep(0.5)

        assert "Connection closed" in str(execinfo.value)


async def test_log_error_response_with_unknown_id(fakes):
    error_msg = "error for unknown call"
    async with capture_jsonrpc_error(fakes) as f:
        # send notification
        await fakes.transport.send_to_jsonrpc(
            id=43, error={"code": 2, "message": error_msg}
        )
    assert error_msg in f.getvalue()


async def test_prevent_call_if_communication_task_exited():
    transport = FakeTransport()
    api = FakeApi()
    channel = jsonrpc.Channel(
        transport.send_to_test, transport.receive_from_test(), api
    )
    async with util.background_task(channel.communicate_forever()) as com_task:
        # send one request, to synchronize com_task startup
        await transport.send_to_jsonrpc(method="set_event")
        await api.event.wait()

        # cancel communicate_forever()
        await util.cancel_tasks([com_task])

        with pytest.raises(RuntimeError) as execinfo:
            await channel.some_func()
        assert "Channel communication is already closed" in str(execinfo.value)
