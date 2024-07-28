#!/usr/bin/env python3

import asyncio
import contextvars
import dataclasses
import functools
import itertools
import json
import logging
import textwrap
import traceback
from typing import Any, Optional, Union

import not_my_board._util as util

logger = logging.getLogger(__name__)
channel_var = contextvars.ContextVar("channel")


CODE_INTERNAL_ERROR = -32603
CODE_INVALID_REQUEST = -32600
CODE_METHOD_NOT_FOUND = -32601


class Channel(util.ContextStack):
    """Send and receive remote procedure calls with JSON RPC

    `send` is an async function that takes messages (bytes) and sends it over
    the wire.
    `receive_iter` is an async iterator, that yields messages (bytes) whenever
    they are available.
    The `api_obj` is used to handle incoming calls. If it has a coroutine with
    a name, that matches with the received method request, then it is called.
    The return value is sent as a response. If the method throws an exception,
    then an error with the traceback is sent to the remote caller.

    To call methods on the remote side, just call a coroutine method with the
    same name on the channel object. The call is automatically converted into a
    remote procedure call. It waits for the result and converts it to a return
    value. If an error is returned, then it is converted back into an
    exception. If the call is canceled, then the remote execution is also
    canceled.
    """

    def __init__(self, send, receive_iter, api_obj=None):
        self._send = send
        self._receive_iter = receive_iter
        self._api_obj = api_obj
        self._tasks = set()
        self._request_tasks_by_id = {}
        self._id_generator = itertools.count(start=1)
        self._pending = {}

        # TODO should be False before communicate_forever() is running
        self._is_receiving = True

    def set_api_object(self, api_obj):
        """Set the API object, that is used to handle incoming calls."""
        self._api_obj = api_obj

    async def communicate_forever(self):
        """This function needs to run in a task, while the channel is used"""
        channel_var.set(self)
        try:
            async for raw_data in self._receive_iter:
                try:
                    await self._receive(raw_data)
                except Exception:
                    traceback.print_exc()
        finally:
            self._is_receiving = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Connection closed"))
            await util.cancel_tasks(self._tasks.copy())

    async def _context_stack(self, stack):
        bg_task = util.background_task(self.communicate_forever())
        await stack.enter_async_context(bg_task)

    def __getattr__(self, method_name):
        if method_name.startswith("_"):
            raise AttributeError(f"invalid attribute '{method_name}'")
        return functools.partial(self._call, method_name)

    async def _receive(self, raw_data):
        info = {"id": None, "is_request": False}
        try:
            message = _parse_message(raw_data, info)
        except Exception as e:
            if info["id"] is not None:
                if info["is_request"]:
                    response = ErrorResponse.with_traceback(
                        info["id"], CODE_INVALID_REQUEST, "Invalid Request"
                    )
                    await self._send(bytes(response))
                    return
                else:
                    future = self._pending.get(info["id"])
                    if future and not future.done():
                        future.set_exception(e)
                        return

            raise

        if isinstance(message, Request):
            task = asyncio.create_task(self._handle_request(message))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif isinstance(message, Response):
            await self._handle_response(message)
        else:  # ErrorResponse
            await self._handle_error_response(message)

    async def _handle_request(self, request):
        next_error = (CODE_METHOD_NOT_FOUND, "Method not found")
        try:
            if request.method == "rpc.cancel":
                method = self._cancel_local
            else:
                if request.method.startswith("_"):
                    raise ProtocolError(f'method "{request.method}" not allowed')

                method = getattr(self._api_obj, request.method)

                if getattr(method, "_jsonrpc_hidden", False):
                    raise ProtocolError(f'method "{request.method}" is marked hidden')

                logger.info("Method call: %s", request.method)

            next_error = CODE_INTERNAL_ERROR, None
            if request.id is not None:
                try:
                    self._request_tasks_by_id[request.id] = asyncio.current_task()
                    result = await method(*request.args, **request.kwargs)
                    response = Response(request.id, result)
                    await self._send(bytes(response))
                finally:
                    del self._request_tasks_by_id[request.id]
            else:
                await method(*request.args, **request.kwargs)
        except Exception as e:
            if request.id is not None:
                code, message = next_error
                if message is None:
                    message = str(e)
                response = ErrorResponse.with_traceback(request.id, code, message)
                await self._send(bytes(response))
            else:
                traceback.print_exc()

    async def _cancel_local(self, id_):
        if id_ in self._request_tasks_by_id:
            await util.cancel_tasks([self._request_tasks_by_id[id_]])

    async def _handle_response(self, response):
        future = self._pending.get(response.id)
        if future and not future.done():
            future.set_result(response.result)

    async def _handle_error_response(self, error_response):
        exc = error_response.as_exception()

        future = self._pending.get(error_response.id)
        if future and not future.done():
            future.set_exception(exc)
        else:
            raise exc

    async def _call(self, method_name, *args, **kwargs):
        if not self._is_receiving:
            raise RuntimeError("Channel communication is already closed")

        if kwargs.pop("_notification", False):
            id_ = None
        else:
            id_ = next(self._id_generator)

        if args and kwargs:
            raise RuntimeError("Use either args or kwargs")

        request = Request(id_, method_name, args or kwargs)
        logger.info("Calling: %s", request.method)

        if id_ is not None:
            return await self._send_request(request)
        else:
            # send notification
            await self._send(bytes(request))

    async def _send_request(self, request, send_cancellation=True):
        future = asyncio.get_running_loop().create_future()
        self._pending[request.id] = future
        try:
            await self._send(bytes(request))
            return await self._pending[request.id]
        except asyncio.CancelledError:
            if send_cancellation:
                logger.info("Canceling: %s", request.method)
                await self._cancel_remote(request.id)
            raise
        finally:
            del self._pending[request.id]

    async def _cancel_remote(self, to_cancel_id):
        id_ = next(self._id_generator)
        request = Request(id_, "rpc.cancel", [to_cancel_id])

        # don't request to cancel the cancellation if this task is canceled
        await self._send_request(request, send_cancellation=False)


def hidden(func):
    """Decorator to mark a function as hidden.

    Hidden functions can't be called by the remote. Use this if you want to
    keep it public for local users, otherwise just use a leading underscore in
    the method name.
    """
    func._jsonrpc_hidden = True
    return func


def get_current_channel():
    return channel_var.get()


def _parse_message(raw_data, info):
    data = json.loads(raw_data)
    id_ = data.get("id")
    if id_ is not None:
        if not isinstance(id_, (str, int)):
            raise ProtocolError('"id" must be a string or number')
        info["id"] = id_

    # check if it is a Request
    method = data.get("method")
    if method is not None:
        info["is_request"] = True

        if not isinstance(method, str):
            raise ProtocolError('"method" must be a string')

        params = data.get("params", [])
        if not isinstance(params, (list, dict)):
            raise ProtocolError('"params" must be a structured value')

        return Request(id_, method, params)

    # must be a Response or ErrorResponse
    if id_ is None:
        raise ProtocolError('"id" is required')

    # check if it is an ErrorResponse
    error = data.get("error")
    if error:
        code = error["code"]
        if not isinstance(code, int):
            raise ProtocolError('"error.code" must be an integer')

        message = error["message"]
        if not isinstance(message, str):
            raise ProtocolError('"error.message" must be a string')

        filtered_error = {
            "code": code,
            "message": message,
            "data": error.get("data"),
        }
        return ErrorResponse(id_, filtered_error)

    # must be a Response
    return Response(id_, data["result"])


@dataclasses.dataclass
class _Message:
    jsonrpc: str = dataclasses.field(default="2.0", init=False)
    id: Optional[Union[int, str]]

    def __bytes__(self):
        body = {}
        for field in dataclasses.fields(self):
            if field.name == "id" and self.id is None:
                # skip optional id
                continue

            body[field.name] = getattr(self, field.name)

        return json.dumps(body).encode()


@dataclasses.dataclass
class Request(_Message):
    method: str
    params: Union[list, dict]

    @property
    def args(self):
        if isinstance(self.params, list):
            return self.params
        return []

    @property
    def kwargs(self):
        if isinstance(self.params, dict):
            return self.params
        return {}


@dataclasses.dataclass
class Response(_Message):
    result: Any


@dataclasses.dataclass
class ErrorResponse(_Message):
    error: dict

    @classmethod
    def with_traceback(cls, id_, code, message):
        error = {
            "code": code,
            "message": message,
            "data": {
                "traceback": traceback.format_exc(),
            },
        }
        return cls(id_, error)

    def as_exception(self):
        return RemoteError(
            self.error["code"], self.error["message"], self.error["data"]
        )


class RemoteError(Exception):
    def __init__(self, code, message, data):
        if isinstance(data, dict) and "traceback" in data:
            details = textwrap.indent(data["traceback"], " " * 4).rstrip()
            super().__init__(f"{message}\n{details}")
        else:
            super().__init__(message)
        self.code = code
        self.data = data


class ProtocolError(Exception):
    pass
