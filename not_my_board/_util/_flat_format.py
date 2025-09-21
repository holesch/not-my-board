import json


def to_flat_format(d, parent_key=""):
    if isinstance(d, dict):
        for k, v in d.items():
            new_key = f"{parent_key}.{k}" if parent_key else k
            yield from to_flat_format(v, new_key)
    elif isinstance(d, list):
        for i, v in enumerate(d):
            yield from to_flat_format(v, f"{parent_key}[{i}]")
    else:
        v = json.dumps(d)
        yield f"{parent_key}={v}"
