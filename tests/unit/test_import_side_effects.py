"""Importing Watchdog must not connect to a database or touch the network.

Not the same as importing nothing happening. ``watchdog.web.app`` ends with
``app = create_app()`` because uvicorn loads an ASGI object by name, so importing
that one module reads the configuration and configures logging on purpose. What
is checked here is the part that holds for every module including that one: no
connection and no network call.

Run in a fresh interpreter so the check cannot be satisfied by modules another
test already imported.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

PROBE = textwrap.dedent(
    """
    # Import these first: patching socket before ssl loads breaks the stdlib.
    import ssl, asyncio, socket, sqlalchemy

    def refuse_network(*args, **kwargs):
        raise AssertionError("network access at import time")

    socket.socket.connect = refuse_network
    socket.create_connection = refuse_network

    def refuse_engine(*args, **kwargs):
        raise AssertionError("database engine created at import time")

    sqlalchemy.create_engine = refuse_engine

    import importlib, pkgutil
    import watchdog

    for module in pkgutil.walk_packages(watchdog.__path__, "watchdog."):
        importlib.import_module(module.name)

    print("clean")
    """
)


def test_importing_every_module_has_no_side_effects() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
