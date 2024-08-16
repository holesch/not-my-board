from typing import Dict, List

import pydantic

UsbId = pydantic.constr(regex=r"^[1-9][0-9]*-[1-9][0-9]*(\.[1-9][0-9]*)*$")


class UsbImportDesc(pydantic.BaseModel):
    port_num: pydantic.NonNegativeInt


class TcpImportDesc(pydantic.BaseModel):
    local_port: pydantic.PositiveInt


class ImportedPart(pydantic.BaseModel):
    compatible: List[str]
    usb: Dict[str, UsbImportDesc] = {}
    tcp: Dict[str, TcpImportDesc] = {}


class ImportDesc(pydantic.BaseModel):
    name: str
    auto_return_time: str = "10h"
    parts: Dict[str, ImportedPart]


class UsbExportDesc(pydantic.BaseModel):
    usbid: UsbId


class TcpExportDesc(pydantic.BaseModel):
    host: str
    port: pydantic.PositiveInt


class ExportedPart(pydantic.BaseModel):
    compatible: List[str]
    usb: Dict[str, UsbExportDesc] = {}
    tcp: Dict[str, TcpExportDesc] = {}


class ExportDesc(pydantic.BaseModel):
    port: pydantic.PositiveInt
    parts: List[ExportedPart]


class Place(ExportDesc):
    id: pydantic.PositiveInt
    # host: pydantic.IPvAnyAddress
    #   can't serialize IP address with json.dumps()
    #   TODO: maybe drop pydantic as a dependency
    host: str
