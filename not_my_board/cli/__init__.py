import argparse
import asyncio
import logging
import pathlib
import json
from not_my_board._serve import serve
from not_my_board._export import export
from not_my_board._agent import agent
import not_my_board._client as client

from ..__about__ import __version__

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


def main():
    logging.basicConfig(format='%(asctime)s %(levelname)s: %(name)s: %(message)s', level=logging.DEBUG)
    logging.getLogger("websockets.client").setLevel(logging.INFO)

    parser = argparse.ArgumentParser(description='Setup, manage and use a board farm')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    subparsers = parser.add_subparsers(
            dest="command", required=True, metavar="command",
            help="for more info use: %(prog)s <command> -h")

    def add_subcommand(name, *args, **kwargs):
        subparser = subparsers.add_parser(name, *args, **kwargs)
        subparser.set_defaults(func=globals()[f"_{name}_command"])
        return subparser

    subparser = add_subcommand("serve", help="start the board farm server")

    subparser = add_subcommand("export", help="make connected boards available in the board farm")
    subparser.add_argument("export_description", type=pathlib.Path, help="path to a export description")

    subparser = add_subcommand("agent", help="start an agent")
    subparser.add_argument("server_url", help="http(s) URL of the server")

    subparser = add_subcommand("reserve", help="reserve a place")
    subparser.add_argument("-n", "--with-name", help="reserve under a different name")
    subparser.add_argument("name", help="name or full path of a place specification")

    subparser = add_subcommand("return", help="return a place")
    subparser.add_argument("name", help="name of the place to return")

    subparser = add_subcommand("attach", help="attach a reserved place")
    subparser.add_argument("-k", "--keep-others", action="store_true", help="don't return all other reservations")
    subparser.add_argument("name", help="name of a reserved place")

    subparser = add_subcommand("detach", help="detach a reserved place")
    subparser.add_argument("-k", "--keep", action="store_true", help="don't return reservation")
    subparser.add_argument("name", help="name of the place to attach")

    subparser = add_subcommand("list", help="list reserved places")

    args = parser.parse_args()

    try:
        obj = args.func(args)
        if asyncio.iscoroutine(obj):
            asyncio.run(obj)
    except KeyboardInterrupt:
        pass


def _serve_command(args):
    serve()

async def _export_command(args):
    await export(args.export_description)

async def _agent_command(args):
    await agent(args.server_url)

async def _reserve_command(args):
    await client.reserve(args.name, args.with_name)

async def _return_command(args):
    await client.return_reservation(args.name)

async def _attach_command(args):
    await client.attach(args.name, args.keep_others)

async def _detach_command(args):
    await client.detach(args.name, args.keep)

async def _list_command(args):
    names = await client.list()
    for name in names:
        print(name)
