import importlib.util
import sys


def lazy_import(name):
    """Import a module, but only execute it on first attribute access.

    Similar to PEP 810 ``lazy import``, but works with all supported Python
    versions. The new syntax can't be used, because the module wouldn't even
    parse with older versions.
    """

    try:
        return sys.modules[name]
    except KeyError:
        pass

    # imports the parent packages eagerly
    spec = importlib.util.find_spec(name)
    if spec is None:
        msg = f"No module named {name!r}"
        raise ModuleNotFoundError(msg, name=name)

    loader = importlib.util.LazyLoader(spec.loader)
    spec.loader = loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)

    parent, _, child = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], child, module)

    return module
