#!/usr/bin/env python3

import asyncio
import contextlib
import dataclasses
import itertools
import logging
import os
import pathlib
import socket
import struct
import sys

import not_my_board._util as util

if sys.version_info < (3, 9):
    from typing_extensions import Annotated
else:
    from typing import Annotated


logger = logging.getLogger(__name__)
_vhci_status_attached = {}


class UsbIpServer:
    def __init__(self, devices):
        self._devices = {d.busid: d for d in devices}

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            for _, device in self._devices.items():
                await stack.enter_async_context(
                    util.background_task(_refresh_task(device))
                )

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    async def handle_client(self, reader, writer):
        sock = writer.transport.get_extra_info("socket")
        _enable_keep_alive(sock)

        request = await ImportRequest.from_reader(reader)
        logger.debug("Received: %s", request)
        if request.busid not in self._devices:
            raise ProtocolError(f"Unexpected Bus ID: {request.busid}")

        device = self._devices[request.busid]

        # allow client to trigger a refresh
        device.refresh()

        async with device:
            writer.transport.pause_reading()
            fd = sock.fileno()
            device.export(fd)

            reply = ImportReply.from_device(device)
            logger.debug("Sending: %s", reply)
            writer.write(bytes(reply))
            await writer.drain()

            writer.close()
            await writer.wait_closed()
            await device.available()


class _SysfsFileInt:
    def __set_name__(self, owner, name):
        self._name = name

    def __init__(self, base=10, default=None):
        self._base = base
        self._default = default

    def __get__(self, instance, owner=None):
        try:
            path = instance._sysfs_path / self._name
            return int(path.read_text(), base=self._base)
        except ValueError:
            if self._default is not None:
                return self._default
            else:
                raise


class _SysfsFileHex(_SysfsFileInt):
    def __init__(self, default=None):
        super().__init__(16, default)


class UsbIpDevice:
    def __init__(self, busid):
        self._busid = busid
        self._sysfs_path = pathlib.Path("/sys/bus/usb/devices/") / busid
        self._lock = asyncio.Lock()
        self._refresh_event = asyncio.Event()
        self._is_exported = False

    def refresh(self):
        self._refresh_event.set()

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(self._lock)
            await self.available()

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._is_exported:
            try:
                (self._sysfs_path / "usbip_sockfd").write_text("-1\n")
            except (OSError, FileNotFoundError):
                # client might have disconnected or device disappeared
                pass
            except Exception as e:
                logger.warning("Error while stopping export: %s", e)

        await self._stack.__aexit__(exc_type, exc, tb)

    async def available(self):
        while True:
            await self._ensure_usbip_host_driver()

            if self._is_available():
                break

            self._refresh_event.clear()
            await self._refresh_event.wait()

    def export(self, fd):
        (self._sysfs_path / "usbip_sockfd").write_text(f"{fd}\n")
        self._is_exported = True

    def _is_available(self):
        try:
            status_available = 1
            if self.usbip_status == status_available:
                logger.debug("Device %s is available", self.busid)
                return True
        except FileNotFoundError:
            # device might have disappeared
            pass
        except Exception as e:
            logger.warning("Error while checking device status: %s", e)

        logger.debug("Device %s is not available, yet", self.busid)
        return False

    async def _ensure_usbip_host_driver(self):
        driver_path = self._sysfs_path / "driver"
        if driver_path.exists():
            driver_name = driver_path.resolve().name
            if driver_name != "usbip-host":
                logger.info(
                    'Unbinding USB device %s from driver "%s"', self._busid, driver_name
                )
                (driver_path / "unbind").write_text(self._busid)
                await self._bind_usbip_host_driver()
        elif self._sysfs_path.exists():
            await self._bind_usbip_host_driver()

    async def _bind_usbip_host_driver(self):
        logger.info('Binding USB device %s to driver "usbip-host"', self._busid)
        usbip_host_driver = pathlib.Path("/sys/bus/usb/drivers/usbip-host")
        if not usbip_host_driver.exists():
            await _exec("modprobe", "usbip-host")
        (usbip_host_driver / "match_busid").write_text(f"add {self._busid}")
        (usbip_host_driver / "bind").write_text(self._busid)

    @property
    def busid(self):
        return self._busid.encode("utf-8")

    @property
    def path(self):
        return self._sysfs_path.as_posix().encode("utf-8")

    @property
    def speed(self):
        string_to_code = {
            "1.5": 1,
            "12": 2,
            "480": 3,
            "53.3-480": 4,
            "5000": 5,
        }
        string = (self._sysfs_path / "speed").read_text()[:-1]  # strip newline
        return string_to_code.get(string, 0)

    usbip_status = _SysfsFileInt()
    busnum = _SysfsFileInt()
    devnum = _SysfsFileInt()
    idVendor = _SysfsFileHex()
    idProduct = _SysfsFileHex()
    bcdDevice = _SysfsFileHex()
    bDeviceClass = _SysfsFileHex()
    bDeviceSubClass = _SysfsFileHex()
    bDeviceProtocol = _SysfsFileHex()
    bConfigurationValue = _SysfsFileHex(default=0)
    bNumConfigurations = _SysfsFileHex()
    bNumInterfaces = _SysfsFileHex(default=0)


async def _exec(*args, **kwargs):
    proc = await asyncio.create_subprocess_exec(*args, **kwargs)
    await proc.communicate()
    if proc.returncode:
        raise RuntimeError(f"{args!r} exited with {proc.returncode}")


async def attach(reader, writer, busid, port_num):
    sock = writer.transport.get_extra_info("socket")
    # Client waits 2 seconds longer before sending keep alive probes, otherwise
    # both sides start sending at the same time.
    _enable_keep_alive(sock, extra_idle_sec=2)

    request = ImportRequest(busid.encode())
    logger.debug("Sending: %s", request)
    writer.write(bytes(request))
    await writer.drain()

    reply = await ImportReply.from_reader(reader)
    logger.debug("Received: %s", reply)

    fd = os.dup(sock.fileno())

    try:
        writer.close()
        await writer.wait_closed()

        attach_path = pathlib.Path("/sys/devices/platform/vhci_hcd.0/attach")
        devid = (reply.busnum << 16) | reply.devnum
        vhci_port = _port_num_to_vhci_port(port_num, reply.speed)
        logger.debug("Attaching USB device to port %d", vhci_port)
        attach_path.write_text(f"{vhci_port} {fd} {devid} {reply.speed}\n")
    finally:
        os.close(fd)

    return vhci_port


def _port_num_to_vhci_port(port_num, speed):
    """Map port_num and speed to port, that is passed to Kernel

    example with vhci_nr_hcs=2 and nports=8:

        vhci_hcd    hub   speed  port_num  vhci_port
        vhci_hcd.0  usb1  hs     0         0
        vhci_hcd.0  usb1  hs     1         1
        vhci_hcd.0  usb2  ss     0         2
        vhci_hcd.0  usb2  ss     1         3
        vhci_hcd.1  usb3  hs     2         4
        vhci_hcd.1  usb3  hs     3         5
        vhci_hcd.1  usb4  ss     2         6
        vhci_hcd.1  usb4  ss     3         7
    """

    platform_path = pathlib.Path("/sys/devices/platform")
    vhci_nr_hcs = len(list(platform_path.glob("vhci_hcd.*")))
    nports = int((platform_path / "vhci_hcd.0/nports").read_text())

    # calculate number of ports each vhci_hcd.* has
    vhci_ports = nports // vhci_nr_hcs
    # calculate number of ports each hub has
    vhci_hc_ports = vhci_ports // 2

    vhci_hcd_nr = port_num // vhci_hc_ports
    vhci_port = (vhci_hcd_nr * vhci_ports) + (port_num % vhci_hc_ports)

    super_speed = 5  # USB_SPEED_SUPER (USB 3.0)
    if speed == super_speed:
        vhci_port += vhci_hc_ports

    if vhci_port >= nports:
        raise RuntimeError(
            f"Configured port_num is out of range. Expected max {(nports // 2) - 1}, got {port_num}"
        )

    return vhci_port


def detach(vhci_port):
    detach_path = pathlib.Path("/sys/devices/platform/vhci_hcd.0/detach")
    try:
        detach_path.write_text(f"{vhci_port}")
    except OSError:
        # not attached anymore
        pass


def refresh_vhci_status():
    def status_paths():
        vhci_path = pathlib.Path("/sys/devices/platform/vhci_hcd.0")
        # the first status path doesn't have a suffix
        status_path = vhci_path / "status"
        count = itertools.count(1)

        while status_path.exists():
            yield status_path
            status_path = vhci_path / f"status.{next(count)}"

    status_attached = 6  # VDEV_ST_USED

    for status_path in status_paths():
        with status_path.open() as f:
            # skip header:
            # hub port sta spd dev      sockfd local_busid
            f.readline()

            for line in f:
                entries = line.split()
                port = int(entries[1])
                status = int(entries[2])
                _vhci_status_attached[port] = status == status_attached


def is_attached(port):
    return _vhci_status_attached[port]


def _enable_keep_alive(sock, extra_idle_sec=0):
    # enable TCP keep alive
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    # Drop connections faster than the 2 hour default: Send first probe after 5
    # (+extra_idle_sec) seconds, then every 5 seconds. After 3 unanswered
    # probes the connection is closed.
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 5 + extra_idle_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)


class StructType:
    def __init__(self, format_str):
        self.format_str = format_str


class StructStr(StructType):
    pass


UInt8 = Annotated[int, StructType("B")]
UInt16 = Annotated[int, StructType("H")]
UInt32 = Annotated[int, StructType("I")]


class Char(bytes):
    def __class_getitem__(cls, key):
        return Annotated[bytes, StructStr(f"{key}s")]


# pylint: disable=W0212,E1101
# - accessing protected member of cls, e.g. cls._struct
# - 'serializable' has no '_struct' member
def serializable(cls):
    cls = dataclasses.dataclass(cls)

    format_str = "!"
    to_strip = set()

    for field in dataclasses.fields(cls):
        for metadata in field.type.__metadata__:
            if isinstance(metadata, StructType):
                format_str += metadata.format_str
                if isinstance(metadata, StructStr):
                    to_strip.add(field.name)
                break
        else:
            raise ProtocolError(f"Field {field!r} not annotated with StructType")

    cls._struct = struct.Struct(format_str)
    cls._to_strip = to_strip

    def __bytes__(self):
        return self._struct.pack(
            *(getattr(self, f.name) for f in dataclasses.fields(self))
        )

    @classmethod
    async def from_reader(cls, reader):
        data = await reader.readexactly(cls._struct.size)
        values = cls._struct.unpack(data)
        init_values = []
        for field, value in zip(dataclasses.fields(cls), values):
            if field.init:
                if field.name in cls._to_strip:
                    value = value.rstrip(b"\0")
                init_values.append(value)
            else:
                if value != field.default:
                    raise ProtocolError(
                        f"Expected {field.name}={field.default}, got={value}"
                    )

        # pylint: disable=E1120
        # No value for argument 'cls': false positive
        return cls(*init_values)

    cls.__bytes__ = __bytes__
    cls.from_reader = from_reader

    return cls


def no_init(default):
    return dataclasses.field(default=default, init=False)


@serializable
class Header:
    version: UInt16 = no_init(0x0111)
    code: UInt16
    status: UInt32 = no_init(0)


@serializable
class ImportRequest(Header):
    code: UInt16 = no_init(0x8003)
    busid: Char[32]


@serializable
class ImportReply(Header):
    code: UInt16 = no_init(0x0003)
    path: Char[256]
    busid: Char[32]
    busnum: UInt32
    devnum: UInt32
    speed: UInt32
    idVendor: UInt16
    idProduct: UInt16
    bcdDevice: UInt16
    bDeviceClass: UInt8
    bDeviceSubClass: UInt8
    bDeviceProtocol: UInt8
    bConfigurationValue: UInt8
    bNumConfigurations: UInt8
    bNumInterfaces: UInt8

    @classmethod
    def from_device(cls, device):
        return cls(
            *(
                getattr(device, field.name)
                for field in dataclasses.fields(cls)
                if field.init
            )
        )


class ProtocolError(Exception):
    pass


async def _refresh_task(device):
    pipe_path = pathlib.Path("/run/usbip-refresh-" + device.busid.decode())

    tmp_path = pipe_path.with_name(pipe_path.name + ".new")
    os.mkfifo(tmp_path)
    tmp_path.replace(pipe_path)

    async with _open_read_pipe(pipe_path, "r+b", buffering=0) as pipe:
        while True:
            await pipe.read(4096)
            device.refresh()


@contextlib.asynccontextmanager
async def _open_read_pipe(*args, **kwargs):
    with open(*args, **kwargs) as pipe:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        await loop.connect_read_pipe(lambda: protocol, pipe)
        yield reader


# pylint: disable=import-outside-toplevel
async def _main():
    import argparse

    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(name)s: %(message)s", level=logging.DEBUG
    )

    parser = argparse.ArgumentParser(description="Import and export USB ports")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    subparser = subparsers.add_parser("export", help="export a USB device")
    subparser.add_argument(
        "busid", help='busid of the device to export, e.g. "1-5.1.4"'
    )
    subparser.add_argument("-p", "--port", default=3240, help="port to listen on")

    subparser = subparsers.add_parser("import", help="import a USB device")
    subparser.add_argument("host", help="host to connect to")
    subparser.add_argument(
        "busid", help='busid of the device to import, e.g. "1-5.1.4"'
    )
    subparser.add_argument(
        "port_num", type=int, help='port to attach device to, e.g. "0"'
    )
    subparser.add_argument("-p", "--port", default=3240, help="port to connect to")

    args = parser.parse_args()

    if args.command == "import":
        while True:
            logger.info("Connecting")
            reader, writer = await asyncio.open_connection(args.host, args.port)
            await attach(reader, writer, args.busid, args.port_num)
    else:
        device = UsbIpDevice(args.busid)
        async with UsbIpServer([device]) as usbip_server:
            server = util.Server(usbip_server.handle_client, port=args.port)
            async with server:
                logger.info("listening")
                await server.serve_forever()


if __name__ == "__main__":
    util.run(_main())
