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

    subparser = add_subcommand("export", help="make connected boards available in the board farm")
    subparser.add_argument("config", type=pathlib.Path, help="configuration of the place to export")

    subparser = add_subcommand("serve", help="start the board farm server")

    subparser = add_subcommand("agent", help="start an agent")

    subparser = add_subcommand("reserve", help="reserve a place")

    subparser = add_subcommand("return", help="return a place")
    subparser.add_argument("place_id", type=int, help="ID of the place to return")

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
    place = json.loads(args.config.read_text())
    await export(place)

async def _agent_command(args):
    await agent()

async def _reserve_command(args):
    await client.reserve()

async def _return_command(args):
    await client.return_reservation(args.place_id)
