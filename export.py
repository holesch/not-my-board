#!/usr/bin/env python3

import socket
import struct
import collections
import dataclasses
import operator
import pathlib

PROTOCOL_VERSION = 0x0111
COMMAND_CODE_IMPORT_REQUEST = 0x8003
COMMAND_CODE_IMPORT_REPLY = 0x0003
COMMAND_CODE_DEVLIST_REQUEST = 0x8005
COMMAND_CODE_DEVLIST_REPLY = 0x0005
STATUS_AVAILABLE = 1

def main():
    busid = "1-5.1.4"

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('localhost', 3240))
    s.listen()

    conn, addr = s.accept()
    with conn:
        process_request(conn, busid)

def process_request(conn, busid):
    sysfs_path = SysfsPath("/sys/bus/usb/devices/") / busid
    stream = StructStream(conn)

    version, code, status = stream.unpack('!HHI')
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"Unexpected protocol version: 0x{version:04x}")
    if status != 0:
        raise ProtocolError(f"Unexpected status: {status}")

    if code == COMMAND_CODE_DEVLIST_REQUEST:
        conn.sendall(create_devlist_reply(sysfs_path))
    elif code == COMMAND_CODE_IMPORT_REQUEST:
        busid_req = stream.unpack('!32s')[0].rstrip(b'\0').decode('utf-8')
        if busid_req != busid:
            raise ProtocolError(f"Unexpected Bus ID: {busid_req}")
        try:
            export_device(sysfs_path, conn)
            conn.sendall(create_import_reply(sysfs_path))
        except Error as e:
            print(e)
            conn.sendall(create_import_error_reply())
    else:
        raise ProtocolError(f"Unexpected command code: 0x{code:04x}")

def create_devlist_reply(sysfs_path):
    struct_desc = [
        ('H', PROTOCOL_VERSION),
        ('H', COMMAND_CODE_DEVLIST_REPLY),
        ('I', 0), # status
        ('I', 1), # n_devices

        *usb_device_struct_desc(sysfs_path)
    ]

    for if_dir in sysfs_path.glob(sysfs_path.name + ':*'):
        struct_desc += [
            ('B', (if_dir / 'bInterfaceClass').read_hex()),
            ('B', (if_dir / 'bInterfaceSubClass').read_hex()),
            ('B', (if_dir / 'bInterfaceProtocol').read_hex()),
            ('B', 0), # padding
        ]

    return struct_desc_to_bytes(struct_desc)

def create_import_reply(sysfs_path):
    return struct_desc_to_bytes([
        ('H', PROTOCOL_VERSION),
        ('H', COMMAND_CODE_IMPORT_REPLY),
        ('I', 0), # status

        *usb_device_struct_desc(sysfs_path)
    ])

def create_import_error_reply():
    return struct_desc_to_bytes([
        ('H', PROTOCOL_VERSION),
        ('H', COMMAND_CODE_IMPORT_REPLY),
        ('I', 1), # status
    ])

def usb_device_struct_desc(sysfs_path):
    return [
        ('256s', sysfs_path.as_posix().encode('utf-8')), # path
        ('32s', sysfs_path.name.encode('utf-8')), # busid

        ('I', (sysfs_path / 'busnum').read_int()),
        ('I', (sysfs_path / 'devnum').read_int()),
        ('I', (sysfs_path / 'speed').read_speed()),

        ('H', (sysfs_path / 'idVendor').read_hex()),
        ('H', (sysfs_path / 'idProduct').read_hex()),
        ('H', (sysfs_path / 'bcdDevice').read_hex()),

        ('B', (sysfs_path / 'bDeviceClass').read_hex()),
        ('B', (sysfs_path / 'bDeviceSubClass').read_hex()),
        ('B', (sysfs_path / 'bDeviceProtocol').read_hex()),
        ('B', (sysfs_path / 'bConfigurationValue').read_hex(default=0)), # can be empty
        ('B', (sysfs_path / 'bNumConfigurations').read_hex()),
        ('B', (sysfs_path / 'bNumInterfaces').read_hex(default=0)), # can be empty
    ]

def struct_desc_to_bytes(struct_desc):
    format_str = "".join(map(operator.itemgetter(0), struct_desc))
    values = map(operator.itemgetter(1), struct_desc)
    return struct.pack(f'!{format_str}', *values)

class StructStream:
    def __init__(self, conn):
        self._conn = conn
        self._data = b''
        self._offset = 0

    def unpack(self, format):
        s = struct.Struct(format)
        while (len(self._data) - self._offset) < s.size:
            data = self._conn.recv(1024)
            if not data:
                break
            self._data += data

        unpacked = s.unpack_from(self._data, self._offset)
        self._offset += s.size
        return unpacked

def export_device(sysfs_path, conn):
    status = (sysfs_path / 'usbip_status').read_int()
    if status != STATUS_AVAILABLE:
        raise Error("Device unavailable")

    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    sockfd = conn.fileno()
    (sysfs_path / 'usbip_sockfd').write_text(f"{sockfd}\n")

class SysfsPath(pathlib.PosixPath):
    def read_int(self, default=None, base=10):
        try:
            return int(self.read_text(), base=base)
        except ValueError:
            if default is not None:
                return default
            else:
                raise

    def read_hex(self, default=None):
        return self.read_int(default=default, base=16)

    def read_speed(self):
        string_to_code = {
            "1.5": 1,
            "12": 2,
            "480": 3,
            "53.3-480": 4,
            "5000": 5,
        }
        string = self.read_text()[:-1] # strip newline
        return string_to_code.get(string, 0)

class ProtocolError(Exception):
    pass

class Error(Exception):
    pass

if __name__ == '__main__':
    main()
