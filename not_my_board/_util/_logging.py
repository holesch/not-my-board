import contextvars
import logging
import sys
import uuid

import colorlog

_PRINTK_PREFIXES = {
    logging.CRITICAL: "<2>",
    logging.ERROR: "<3>",
    logging.WARNING: "<4>",
    logging.INFO: "<6>",
    logging.DEBUG: "<7>",
}
_request_id = contextvars.ContextVar("request_id")


def configure_logging(level):
    # reduce level of verbose loggers
    logging.getLogger("websockets.client").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    if len(root.handlers) == 0:
        handler = logging.StreamHandler()

        if sys.stderr.isatty():
            formatter = colorlog.ColoredFormatter(
                "%(asctime)s %(light_black)s%(request_id)s%(name)s %(log_color)s%(message)s",
                log_colors={
                    "DEBUG": "light_black",
                    "INFO": "reset",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            )
        else:
            formatter = _PrintKFormatter(
                "%(level_prefix)s%(request_id)s%(name)s %(message)s"
            )

        handler.setFormatter(formatter)
        handler.addFilter(_inject_request_id)
        root.addHandler(handler)


class _PrintKFormatter(logging.Formatter):
    def format(self, record):
        record.level_prefix = _PRINTK_PREFIXES.get(record.levelno, "")
        return super().format(record)


def generate_log_request_id():
    _request_id.set(str(uuid.uuid4()))


def _inject_request_id(record):
    request_id = _request_id.get(None)
    record.request_id = f"{request_id} " if request_id else ""
    return True
