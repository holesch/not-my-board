#!/usr/bin/env python3

import asyncio
import socket
import pathlib
import struct
import contextlib
import logging
import traceback


logger = logging.getLogger(__name__)


PROTOCOL_VERSION = 0x0111
COMMAND_CODE_IMPORT_REQUEST = 0x8003
COMMAND_CODE_IMPORT_REPLY = 0x0003
COMMAND_CODE_DEVLIST_REQUEST = 0x8005
COMMAND_CODE_DEVLIST_REPLY = 0x0005
STATUS_AVAILABLE = 1


class _SysfsFileInt:
    def __init__(self, name, base=10, default=None):
        self._name = name
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
    def __init__(self, name, default=None):
        super().__init__(name, 16, default)


class UsbIpDevice:
    def __init__(self, busid):
        self._busid = busid
        self._sysfs_path = pathlib.Path("/sys/bus/usb/devices/") / busid
        self._lock = asyncio.Lock()
        self._refresh_event = asyncio.Event()
        self._is_exported = False

    async def handle_client(self, reader, writer):
        try:
            await _UsbIpConnection(self, reader, writer).handle_client()
        except Exception:
            traceback.print_exc()

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
                (self._sysfs_path / 'usbip_sockfd').write_text("-1\n")
            except (OSError, FileNotFoundError):
                # client might have disconnected or device disappeared
                pass
            except Exception as e:
                logger.warn(f"Error while stopping export: {e}")

        await self._stack.__aexit__(exc_type, exc, tb)

    async def available(self):
        while not self._is_available():
            self._refresh_event.clear()
            await self._refresh_event.wait()

    def export(self, fd):
        (self._sysfs_path / 'usbip_sockfd').write_text(f"{fd}\n")
        self._is_exported = True

    def _is_available(self):
        try:
            if self.usbip_status == STATUS_AVAILABLE:
                return True
        except FileNotFoundError:
            # device might have disappeared
            pass
        except Exception as e:
            logger.warn(f"Error while checking device status: {e}")

        return False

    @property
    def busid(self):
        return self._sysfs_path.name.encode('utf-8')

    @property
    def path(self):
        return self._sysfs_path.as_posix().encode('utf-8')

    @property
    def speed(self):
        string_to_code = {
            "1.5": 1,
            "12": 2,
            "480": 3,
            "53.3-480": 4,
            "5000": 5,
        }
        string = (self._sysfs_path / 'speed').read_text()[:-1]  # strip newline
        return string_to_code.get(string, 0)

    usbip_status = _SysfsFileInt('usbip_status')
    busnum = _SysfsFileInt('busnum')
    devnum = _SysfsFileInt('devnum')
    idVendor = _SysfsFileHex('idVendor')
    idProduct = _SysfsFileHex('idProduct')
    bcdDevice = _SysfsFileHex('bcdDevice')
    bDeviceClass = _SysfsFileHex('bDeviceClass')
    bDeviceSubClass = _SysfsFileHex('bDeviceSubClass')
    bDeviceProtocol = _SysfsFileHex('bDeviceProtocol')
    bConfigurationValue = _SysfsFileHex('bConfigurationValue', default=0)
    bNumConfigurations = _SysfsFileHex('bNumConfigurations')
    bNumInterfaces = _SysfsFileHex('bNumInterfaces', default=0)

    def interfaces(self):
        for if_dir in self._sysfs_path.glob(self._sysfs_path.name + ':*'):
            yield _UsbIpDeviceInterface(if_dir)


class _UsbIpDeviceInterface:
    def __init__(self, sysfs_path):
        self._sysfs_path = sysfs_path

    bInterfaceClass = _SysfsFileHex('bInterfaceClass')
    bInterfaceSubClass = _SysfsFileHex('bInterfaceSubClass')
    bInterfaceProtocol = _SysfsFileHex('bInterfaceProtocol')


class _UsbIpConnection:
    def __init__(self, device, reader, writer):
        self._device = device
        self._reader = reader
        self._data = b''
        self._writer = writer

    async def handle_client(self):
        version, code, status = await self._receive('!HHI')
        if version != PROTOCOL_VERSION:
            raise ProtocolError(f"Unexpected protocol version: 0x{version:04x}")
        if status != 0:
            raise ProtocolError(f"Unexpected status: {status}")

        if code == COMMAND_CODE_DEVLIST_REQUEST:
            await self._send(self._devlist_reply())
        elif code == COMMAND_CODE_IMPORT_REQUEST:
            busid = (await self._receive('!32s'))[0].rstrip(b'\0')
            if busid != self._device.busid:
                raise ProtocolError(f"Unexpected Bus ID: {busid}")

            async with self._device:
                self._writer.transport.pause_reading()
                fd = self._writer.transport.get_extra_info("socket").fileno()
                self._device.export(fd)
                await self._send(self._import_reply())
                await self._device.available()
        else:
            raise ProtocolError(f"Unexpected command code: 0x{code:04x}")

    async def _receive(self, format):
        s = struct.Struct(format)
        while len(self._data) < s.size:
            data = await self._reader.read(4 * 1024)
            if not data:
                break
            self._data += data

        unpacked = s.unpack_from(self._data)
        self._data = self._data[s.size:]
        return unpacked

    async def _send(self, data):
        self._writer.write(bytes(data))
        await self._writer.drain()

    def _devlist_reply(self):
        reply = _StructBuilder(
            ('H', PROTOCOL_VERSION),
            ('H', COMMAND_CODE_DEVLIST_REPLY),
            ('I', 0),  # status
            ('I', 1),  # n_devices

            *self._usb_device_desc()
        )

        for interface in self._device.interfaces():
            reply.append(
                ('B', interface.bInterfaceClass),
                ('B', interface.bInterfaceSubClass),
                ('B', interface.bInterfaceProtocol),
                ('B', 0),  # padding
            )

        return reply

    def _import_reply(self):
        return _StructBuilder(
            ('H', PROTOCOL_VERSION),
            ('H', COMMAND_CODE_IMPORT_REPLY),
            ('I', 0),  # status

            *self._usb_device_desc()
        )

    def _usb_device_desc(self):
        return [
            ('256s', self._device.path),
            ('32s', self._device.busid),

            ('I', self._device.busnum),
            ('I', self._device.devnum),
            ('I', self._device.speed),

            ('H', self._device.idVendor),
            ('H', self._device.idProduct),
            ('H', self._device.bcdDevice),

            ('B', self._device.bDeviceClass),
            ('B', self._device.bDeviceSubClass),
            ('B', self._device.bDeviceProtocol),
            ('B', self._device.bConfigurationValue),
            ('B', self._device.bNumConfigurations),
            ('B', self._device.bNumInterfaces),
        ]


class _StructBuilder:
    def __init__(self, *type_value_pairs):
        self._format = '!'
        self._values = list()
        self.append(*type_value_pairs)

    def append(self, *type_value_pairs):
        for type_, value in type_value_pairs:
            self._format += type_
            self._values.append(value)

    def __bytes__(self):
        return struct.pack(self._format, *self._values)


class ProtocolError(Exception):
    pass


@contextlib.asynccontextmanager
async def _open_read_pipe(*args, **kwargs):
    with open(*args, **kwargs) as pipe:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        await loop.connect_read_pipe(lambda: protocol, pipe)
        yield reader


async def _watch_refresh_pipe(pipe, device):
    while True:
        await pipe.read(4096)
        device.refresh()


async def _main():
    import os

    busid = "1-5.1.1.1.4"
    device = UsbIpDevice(busid)
    server = await asyncio.start_server(device.handle_client, port=3240, family=socket.AF_INET)
    async with server:
        pipe_path = pathlib.Path("/run/usbip-refresh-" + busid)

        tmp_path = pipe_path.with_name(pipe_path.name + ".new")
        os.mkfifo(tmp_path)
        tmp_path.replace(pipe_path)

        async with _open_read_pipe(pipe_path, "r+b", buffering=0) as pipe:
            tasks = [asyncio.create_task(coro) for coro in [
                        server.serve_forever(),
                        _watch_refresh_pipe(pipe, device),
                    ]]

            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()


if __name__ == '__main__':
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
