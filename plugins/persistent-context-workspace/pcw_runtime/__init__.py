"""PCW local memory runtime. Importing this package does not load model weights."""

__all__ = ["CycleRuntime", "PCWRuntime", "PCWError", "SearchConfig", "select_candidates"]


def __getattr__(name):
    if name == "CycleRuntime":
        from .cycles import CycleRuntime
        return CycleRuntime
    if name in __all__:
        from . import runtime
        return getattr(runtime, name)
    raise AttributeError(name)
