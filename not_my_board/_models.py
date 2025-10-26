import dataclasses
import re
from typing import Annotated, get_args, get_origin


class TypeValidator:
    pass


class ValueRange(TypeValidator):
    def __init__(self, min_=None, max_=None):
        self._min = min_
        self._max = max_

    def __call__(self, value: int):
        if self._min is not None and value < self._min:
            raise ValueError(f"Value {value} is less than minimum {self._min}")
        if self._max is not None and value > self._max:
            raise ValueError(f"Value {value} is greater than maximum {self._max}")


class Regex(TypeValidator):
    def __init__(self, pattern: str):
        self._pattern = pattern
        self._regex = re.compile(pattern)

    def __call__(self, value: str):
        if not self._regex.fullmatch(value):
            raise ValueError(f"Value {value} does not match pattern {self._pattern}")


@dataclasses.dataclass
class BaseModel:
    @classmethod
    def init_recursive(cls, **kwargs):
        init_kwargs = {}
        for f in dataclasses.fields(cls):
            if f.name not in kwargs:
                continue

            init_kwargs[f.name] = cls._init_arg(kwargs[f.name], f.type)

        return cls(**init_kwargs)

    @classmethod
    def _init_arg(cls, value, type_hint):
        origin = get_origin(type_hint)
        if origin is Annotated:
            return cls._init_arg(value, type_hint.__origin__)

        if origin is list:
            (item_type,) = get_args(type_hint)
            return [cls._init_arg(v, item_type) for v in value]

        if origin is dict:
            key_type, val_type = get_args(type_hint)
            return {
                cls._init_arg(k, key_type): cls._init_arg(v, val_type)
                for k, v in value.items()
            }

        if issubclass(type_hint, BaseModel):
            return type_hint.init_recursive(**value)

        return value

    def __post_init__(self):
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            self._validate_field(field.name, value, field.type)

    def _validate_field(self, path, value, type_hint, metadata=()):
        origin = get_origin(type_hint)

        if origin is Annotated:
            base_type, *metadata = get_args(type_hint)
            self._validate_field(path, value, base_type, metadata)
            return

        if not isinstance(value, origin or type_hint):
            raise TypeError(
                f"{path}: Expected {type_hint.__name__}, got {type(value).__name__}"
            )

        for validator in metadata:
            if isinstance(validator, TypeValidator):
                try:
                    validator(value)
                except Exception as e:
                    raise ValueError(f"{path}: Invalid value") from e

        if origin is list:
            (item_type,) = get_args(type_hint)
            for idx, item in enumerate(value):
                self._validate_field(f"{path}[{idx}]", item, item_type)
        elif origin is dict:
            key_type, val_type = get_args(type_hint)
            for k, v in value.items():
                self._validate_field(f"{path}[key={k}]", k, key_type)
                self._validate_field(f"{path}[{k}]", v, val_type)

    def dict(self):
        return dataclasses.asdict(self)


NonNegativeInt = Annotated[int, ValueRange(min_=0)]
PositiveInt = Annotated[int, ValueRange(min_=1)]
UsbId = Annotated[str, Regex(r"[1-9][0-9]*-[1-9][0-9]*(\.[1-9][0-9]*)*")]


@dataclasses.dataclass
class UsbImportDesc(BaseModel):
    port_num: NonNegativeInt


@dataclasses.dataclass
class TcpImportDesc(BaseModel):
    local_port: PositiveInt


@dataclasses.dataclass
class ImportedPart(BaseModel):
    compatible: list[str]
    usb: dict[str, UsbImportDesc] = dataclasses.field(default_factory=dict)
    tcp: dict[str, TcpImportDesc] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class ImportDesc(BaseModel):
    name: str
    parts: dict[str, ImportedPart]
    auto_return_time: str = "10h"


@dataclasses.dataclass
class UsbExportDesc(BaseModel):
    usbid: UsbId


@dataclasses.dataclass
class TcpExportDesc(BaseModel):
    host: str
    port: PositiveInt


@dataclasses.dataclass
class ExportedPart(BaseModel):
    compatible: list[str]
    usb: dict[str, UsbExportDesc] = dataclasses.field(default_factory=dict)
    tcp: dict[str, TcpExportDesc] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class ExportDesc(BaseModel):
    name: str
    port: PositiveInt
    parts: list[ExportedPart]


@dataclasses.dataclass
class Place(ExportDesc):
    id: PositiveInt
    host: str
