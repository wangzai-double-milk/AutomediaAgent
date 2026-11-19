"""Stable CLI and import entry for the GenericAgent kernel."""
import sys
if __name__ == "__main__":
    import runpy
    runpy.run_module("automedia.legacy.agentmain", run_name="__main__")
else:
    from automedia.legacy import agentmain as _implementation
    sys.modules[__name__] = _implementation
