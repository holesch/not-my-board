def __getattr__(name):
    # import the hub on demand, so that the CLI doesn't need to load it
    if name == "asgi_app":
        from ._hub import asgi_app  # noqa: PLC0415

        return asgi_app

    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
