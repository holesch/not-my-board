import argparse
import aiohttp.web
import functools
import asyncio
import not_my_board.forward

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

        await not_my_board.forward.forward_connection(request.transport, 8080)

        return

    return await handler(request)


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
