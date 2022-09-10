import argparse
import aiohttp.web
import functools
import asyncio

from ..__about__ import __version__


routes = aiohttp.web.RouteTableDef()


def main():
    parser = argparse.ArgumentParser(description='Setup, manage and use a board farm')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    subparsers = parser.add_subparsers(
            dest="command", required=True, metavar="command",
            help="for more info use: %(prog)s <command> -h")

    subparser = subparsers.add_parser("export", help="make connected boards available in the board farm")
    subparser.set_defaults(func=export_command)

    args = parser.parse_args()

    try:
        args.func(args)
    except KeyboardInterrupt:
        pass


def export_command(args):
    app = aiohttp.web.Application(middlewares=[middleware_debug])
    app["config"] = parse_config()
    app.add_routes(routes)
    aiohttp.web.run_app(app, host='localhost', port=2092)


@aiohttp.web.middleware
async def middleware_debug(request, handler):
    print(f'debug: {request=}')

    if request.method == "CONNECT":
        resp = aiohttp.web.StreamResponse()
        resp._length_check = False
        await resp.prepare(request)
        resp.force_close()

        loop = asyncio.get_running_loop()
        on_con_lost = loop.create_future()
        protocol = DummyHTTPResponse(on_con_lost)
        protocol.connection_made(request.transport)
        request.transport.set_protocol(protocol)
        await on_con_lost
        return

    return await handler(request)

class DummyHTTPResponse(asyncio.Protocol):
    def __init__(self, on_con_lost):
        self.on_con_lost = on_con_lost

    def connection_made(self, transport):
        peername = transport.get_extra_info('peername')
        print('Connection from {}'.format(peername))
        self.transport = transport

    def data_received(self, data):
        message = data.decode()
        print('Data received: {!r}'.format(message))

        body = "Hello, World!\n"
        response = "\r\n".join([
            "HTTP/1.1 200 Success",
            "Content-Type: text/plain",
            f"Content-Length: {len(body)}",
            "Server: Fake CONNECT",
            "",
            body
        ])
        self.transport.write(response.encode())

        print('Close the client socket')
        self.transport.close()

    def connection_lost(self, exc):
        print('The client closed the connection')
        self.on_con_lost.set_result(True)


def parse_config():
    # TODO: parse real config file
    return {
        "places": {
            "0": {
                "boards": {
                    "example-board": {
                        "interfaces": {
                            "usb0": {
                                "type": "usb",
                            }
                        },
                    },
                },
            },
        }
    }


def json_get(path):
    def decorator(f):
        @routes.get(path)
        @functools.wraps(f)
        async def wrapper(*args, **kwargs):
            response = await f(*args, **kwargs)
            return aiohttp.web.json_response(response)
        return wrapper
    return decorator


@json_get('/api/v1/places')
async def handle_places(request):
    config = request.app["config"]
    return { "places": config.get("places", {}) }

# /api/v1/places
# /api/v1/places/{place}
# /api/v1/places/{place}/shared/wifi
# /api/v1/places/{place}/boards/{board}
# /api/v1/places/{place}/boards/{board}/interfaces
# /api/v1/places/{place}/boards/{board}/interfaces/{interface}

# /api/v1/places/{place}/boards/{board}/usb0
# /api/v1/places/{place}/boards/{board}/usb-serial
# /api/v1/places/{place}/boards/{board}/control
