#!/usr/bin/env python3

import asyncio
import json
import traceback
import functools
import textwrap
import logging


logger = logging.getLogger(__name__)


CODE_INTERNAL_ERROR = -32603
CODE_PARSE_ERROR = -32700
CODE_INVALID_REQUEST = -32600
CODE_METHOD_NOT_FOUND = -32601


class RemoteError(Exception):
    def __init__(self, code, message, data):
        if "traceback" in data:
            details = textwrap.indent(data["traceback"], " " * 4).rstrip()
            super().__init__(f"{message}\n{details}")
        else:
            super().__init__(message)
        self.code = code
        self.data = data


class Server:
    def __init__(self, send, receive_iter, api_obj):
        super().__init__()
        self._send = send
        self._receive_iter = receive_iter
        self._api_obj = api_obj

    async def serve_forever(self):
        async for raw_data in self._receive_iter:
            await self._receive(raw_data)

    async def _receive(self, raw_data):
        id_ = None
        next_error = (CODE_PARSE_ERROR, "Parse Error")
        try:
            id_, data = Request.parse_id(raw_data)

            next_error = (CODE_INVALID_REQUEST, "Invalid Request")
            request = Request.from_data(data)

            next_error = (CODE_METHOD_NOT_FOUND, "Method not found")
            assert not request.method.startswith("_")
            method = getattr(self._api_obj, request.method)

            next_error = CODE_INTERNAL_ERROR, None
            logger.info(f"Method call: {request.method}")
            result = await method(*request.args, **request.kwargs)

            if id_ is not None:
                response = Response(result, id_)
                await self._send(bytes(response))
        except Exception as e:
            if id_ is not None:
                code, message = next_error
                if message is None:
                    message = str(e)
                response = ErrorResponse.with_traceback(code, message, id_)
                await self._send(bytes(response))
            else:
                traceback.print_exc()


class Proxy:
    def __init__(self, send, receive_iter):
        self._send = send
        self._receive_iter = receive_iter
        self._next_id = 1
        self._pending = dict()
        self._is_receiving = True

    async def __aenter__(self):
        self._task = asyncio.create_task(self.io_loop())
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def io_loop(self):
        try:
            async for raw_data in self._receive_iter:
                await self._receive(raw_data)
        finally:
            self._is_receiving = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Connection closed"))

    async def _receive(self, raw_data):
        id_ = None
        try:
            id_, data = Response.parse_id(raw_data)
            response = Response.from_data(data)

            future = self._pending.get(id_)
            if future and not future.done():
                future.set_result(response.result)
        except Exception as e:
            future = self._pending.get(id_)
            if future and not future.done():
                future.set_exception(e)
            else:
                traceback.print_exc()

    def __getattr__(self, method_name):
        if method_name.startswith("_"):
            raise AttributeError("invalid attribute '%s'" % method_name)
        return functools.partial(self._call, method_name)

    async def _call(self, method_name, *args, **kwargs):
        if not self._is_receiving:
            raise RuntimeError("IO loop is not running, can't receive responses")

        if kwargs.pop('_notification', False):
            id_ = None
        else:
            id_ = self._next_id
            self._next_id += 1

        assert not args or not kwargs, "use either args or kwargs"

        request = Request(method_name, args or kwargs, id_)
        logger.info(f"Calling: {request.method}")

        if id_ is not None:
            future = asyncio.get_running_loop().create_future()
            self._pending[id_] = future
            try:
                await self._send(bytes(request))
                return await self._pending[id_]
            finally:
                del self._pending[id_]
        else:
            await self._send(bytes(request))


class Message:
    _is_id_required = False

    @classmethod
    def parse_id(cls, raw_data):
        data = json.loads(raw_data)
        if cls._is_id_required or "id" in data:
            assert isinstance(data["id"], (str, int)), \
                    "\"id\" must be a string or number"
        return data.get("id"), data

    def __bytes__(self):
        return json.dumps({
            "jsonrpc": "2.0",
            **self._body,
        }).encode()


class Request(Message):
    def __init__(self, method, params, id_=None):
        self.method = method
        self.id = id_
        self._body = {
            "method": method,
            "params": params,
        }

        if id_:
            self._body["id"] = id_

        if isinstance(params, list):
            self.args = params
            self.kwargs = {}
        else:
            self.args = []
            self.kwargs = params

    @classmethod
    def from_data(cls, data):
        method = data["method"]
        assert isinstance(method, str), "\"method\" must be a string"
        params = data.get("params", list())
        assert isinstance(params, (list, dict)), \
            "\"params\" must be a structured value"
        return cls(method, params, data.get("id"))


class Response(Message):
    _is_id_required = True

    def __init__(self, result, id_):
        self._body = {
            "result": result,
            "id": id_
        }
        self.result = result
        self.id = id_

    @classmethod
    def from_data(cls, data):
        if "error" in data:
            error = data["error"]
            raise RemoteError(error["code"], error["message"], error["data"])
        return cls(data["result"], data["id"])


class ErrorResponse(Response):
    def __init__(self, code, message, id_, data=None):
        self._body = {
            "error": {
                "code": code,
                "message": message,
            },
            "id": id_
        }
        if data is not None:
            self._body["error"]["data"] = data

    @classmethod
    def with_traceback(cls, code, message, id_):
        data = {"traceback": traceback.format_exc()}
        return cls(code, message, id_, data)