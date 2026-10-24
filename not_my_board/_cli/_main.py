# ruff: noqa: T201
import argparse
import asyncio
import json
import logging
import pathlib
import sys

import shtab

import not_my_board._util as util

from . import _completion as completion

# only load the modules required by the selected subcommand
agent = util.lazy_import("not_my_board._agent")
auth = util.lazy_import("not_my_board._auth")
client = util.lazy_import("not_my_board._client")
export = util.lazy_import("not_my_board._export")
http = util.lazy_import("not_my_board._http")
hub = util.lazy_import("not_my_board._hub")
tabulate = util.lazy_import("tabulate")

try:
    from ..__about__ import __version__
except ModuleNotFoundError:
    __version__ = "dev"


TOKEN_STORE_PATH = "/var/lib/not-my-board/auth_tokens.json"  # noqa: S105
logger = logging.getLogger(__name__)


# ruff: noqa: PLR0915
def main():
    parser = argparse.ArgumentParser(description="Setup, manage and use a board farm")
    shtab.add_argument_to(
        parser, ["-s", "--print-completion"], preamble=completion.get_shell_preambles()
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="command",
        help="for more info use: %(prog)s <command> -h",
    )

    def add_subcommand(name, *args, **kwargs):
        subparser = subparsers.add_parser(name, *args, **kwargs)
        subparser.set_defaults(
            func=globals()[f"_{name}_command"], log_level=logging.WARNING
        )
        subparser.add_argument(
            "-v", "--verbose", action="store_true", help="Enable debug logs"
        )
        return subparser

    def add_cacert_arg(subparser):
        subparser.add_argument(
            "--cacert",
            type=pathlib.Path,
            action="append",
            help="path to CA certificates, which should be trusted",
        ).complete = shtab.FILE

    subparser = add_subcommand("hub", help="start the board farm hub")
    subparser.set_defaults(log_level=logging.INFO)

    subparser = add_subcommand(
        "export", help="make connected boards available in the board farm"
    )
    subparser.set_defaults(log_level=logging.INFO)
    add_cacert_arg(subparser)
    subparser.add_argument("--token-cmd", help="generate ID tokens with shell command")
    subparser.add_argument("hub_url", help="http(s) URL of the hub")
    subparser.add_argument(
        "export_description", type=pathlib.Path, help="path to a export description"
    ).complete = shtab.FILE

    subparser = add_subcommand("agent", help="start an agent")
    subparser.set_defaults(log_level=logging.INFO)
    add_cacert_arg(subparser)
    subparser.add_argument("--token-cmd", help="generate ID tokens with shell command")
    subparser.add_argument(
        "--fd", type=int, help="listen on socket from this file descriptor"
    )
    subparser.add_argument("hub_url", help="http(s) URL of the hub")

    subparser = add_subcommand("reserve", help="reserve a place")
    subparser.add_argument("-n", "--with-name", help="reserve under a different name")
    subparser.add_argument(
        "import_description", help="name or full path of an import description"
    ).complete = completion.import_descriptions_with_places()

    subparser = add_subcommand("return", help="return a place")
    subparser.add_argument("name", help="name of the place to return").complete = (
        completion.reservations()
    )

    subparser = add_subcommand("attach", help="attach a reserved place")
    subparser.add_argument(
        "-k",
        "--keep-others",
        action="store_true",
        help="don't return all other reservations",
    )
    subparser.add_argument(
        "name", help="name of a reserved place or an import description"
    ).complete = completion.reservations_or_import_descriptions()

    subparser = add_subcommand("detach", help="detach a reserved place")
    subparser.add_argument(
        "-k", "--keep", action="store_true", help="don't return reservation"
    )
    subparser.add_argument(
        "name", nargs="*", help="names of places to detach (default: all)"
    ).complete = completion.reservations()

    subparser = add_subcommand("list", help="list reserved places")
    subparser.add_argument(
        "-n", "--no-header", action="store_true", help="Hide table header"
    )

    subparser = add_subcommand("search", help="list available boards")
    subparser.add_argument(
        "import_description",
        nargs="?",
        help="name or full path of an import description",
    ).complete = completion.import_descriptions()

    # internal subcommand for shell completion, hidden from help
    subparser = add_subcommand("imports")

    subparser = add_subcommand("show", help="show place")
    subparser.add_argument(
        "-j", "--json", action="store_true", help="format output as JSON"
    )
    subparser.add_argument(
        "name", help="name of a reserved place or a place name"
    ).complete = completion.reservations_or_places()

    subparser = add_subcommand(
        "status", help="show status of attached places and its interfaces"
    )
    subparser.add_argument(
        "-n", "--no-header", action="store_true", help="Hide table header"
    )

    subparser = add_subcommand("uevent", help="handle Kernel uevent for USB devices")
    subparser.add_argument("devpath", help="devpath attribute of uevent")

    subparser = add_subcommand("login", help="log in to a hub")
    add_cacert_arg(subparser)
    subparser.add_argument("hub_url", help="http(s) URL of the hub")

    subparser = add_subcommand("edit", help="edit a reserved place")
    subparser.add_argument("name", help="name of the place to edit").complete = (
        completion.reservations()
    )

    subparser = add_subcommand("who", help="show who has currently reserved a place")
    subparser.add_argument(
        "-n", "--no-header", action="store_true", help="Hide table header"
    )

    args = parser.parse_args()

    # Don't use escape sequences, if stdout is not a tty
    if not sys.stdout.isatty():
        for attr in dir(Format):
            if not attr.startswith("_"):
                setattr(Format, attr, "")

    level = logging.DEBUG if args.verbose else args.log_level
    util.configure_logging(level)

    try:
        obj = args.func(args)
        if asyncio.iscoroutine(obj):
            util.run(obj, debug=args.verbose)
    except KeyboardInterrupt:
        pass


def _hub_command(_):
    hub.run_hub()


async def _export_command(args):
    http_client = http.Client(args.cacert)
    token_src = _token_src(args, http_client)

    async with export.Exporter(
        args.hub_url, args.export_description, http_client, token_src
    ) as exporter:
        await exporter.register_place()
        print("ready", flush=True)
        await exporter.serve_forever()


async def _agent_command(args):
    http_client = http.Client(args.cacert)
    io = agent.AgentIO(args.hub_url, http_client, args.fd)
    token_src = _token_src(args, http_client)

    async with agent.Agent(args.hub_url, io, token_src) as agent_:
        if args.fd is None:
            print("ready", flush=True)
        await agent_.serve_forever()


def _token_src(args, http_client):
    if args.token_cmd:
        return auth.IdTokenFromCmd(args.hub_url, http_client, args.token_cmd)
    return auth.IdTokenFromFile(args.hub_url, http_client, TOKEN_STORE_PATH)


async def _reserve_command(args):
    await client.reserve(args.import_description, args.with_name)


async def _return_command(args):
    await client.return_reservation(args.name)


async def _attach_command(args):
    await client.attach(args.name, args.keep_others)


async def _detach_command(args):
    await client.detach(args.name, args.keep)


async def _list_command(args):
    place_list = await client.list_()
    headers = ["Place", "Status"]

    def row(entry):
        name = f"{entry['place']}@{entry['place_name']}"
        status = (
            f"{Format.GREEN}Attached"
            if entry["attached"]
            else f"{Format.YELLOW}Reserved"
        )
        status += Format.RESET
        return [name, status]

    _print_table(args, headers, place_list, row)


async def _status_command(args):
    status_list = await client.status()
    headers = ["Place", "Part", "Type", "Interface", "Status", "Port"]

    def row(entry):
        keys = ["place", "part", "type", "interface", "port"]
        row = [entry[k] for k in keys]
        status = f"{Format.GREEN}Up" if entry["attached"] else f"{Format.RED}Down"
        status += Format.RESET
        row.insert(-1, status)
        return row

    _print_table(args, headers, status_list, row)


async def _uevent_command(args):
    await client.uevent(args.devpath)


async def _login_command(args):
    http_client = http.Client(args.cacert)
    async with auth.LoginFlow(args.hub_url, http_client, TOKEN_STORE_PATH) as login:
        print(
            f"{Format.BOLD}"
            "Open the following link in your browser and log in:"
            f"{Format.RESET}"
        )
        print(login.login_url)

        if args.cacert:
            print(
                f"{Format.YELLOW}"
                "You might need to accept an unknown certificate in the browser."
                f"{Format.RESET}"
            )

        claims = await login.finish()

        msg = "Login was successful"
        if claims:
            msg += ", your token has the following claims:"

        print(f"{Format.GREEN}{Format.BOLD}{msg}{Format.RESET}")
        for key, value in claims.items():
            print(f"{Format.BOLD}{key}: {Format.RESET}{value}")


async def _edit_command(args):
    await client.edit(args.name)


async def _search_command(args):
    result = await client.search(args.import_description)
    names = [entry["name"] for entry in result]
    for name in sorted(names):
        print(f"@{name}")


def _imports_command(_):
    for name in sorted(client.list_import_descriptions()):
        print(name)


async def _show_command(args):
    place = await client.get_place(args.name)
    if args.json:
        print(json.dumps(place, indent=2))
    else:
        for key, value in util.to_flat_format(place):
            value_fmt = Format.GREEN if isinstance(value, str) else Format.MAGENTA
            value_str = json.dumps(value)
            print(
                f"{Format.BOLD}{key}{Format.RESET}"
                f"={value_fmt}{value_str}{Format.RESET}"
            )


async def _who_command(args):
    reservations = await client.get_reservations()
    headers = ["Place", "User"]

    def row(r):
        return [f'@{r["place_name"]}', r["user"]]

    _print_table(args, headers, reservations, row)


def _print_table(args, headers, entries, row_func):
    if entries:
        if args.no_header:
            headers = []
        else:
            headers[0] = f"{Format.BOLD}{headers[0]}"
            headers[-1] = f"{headers[-1]}{Format.RESET}"

        rows = [row_func(entry) for entry in entries]
        print(tabulate.tabulate(rows, headers=headers, tablefmt="plain"))


class Format:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
