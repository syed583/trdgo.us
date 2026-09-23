"""
Import-order guard for ib_insync on Python 3.12+.

``eventkit`` (an ib_insync dependency) grabs a loop at import time with
``asyncio.get_event_loop_policy().get_event_loop()``. Python 3.14 no longer
creates one implicitly, so that import raises
``RuntimeError: There is no current event loop``.

Note this is the *policy's* current loop, not the running one:
``asyncio.get_event_loop()`` inside a coroutine returns the running loop and
would wrongly look healthy, while the policy still has nothing set. uvicorn
imports the ASGI app from inside ``asyncio.run``, which hits exactly that
case, so the check below deliberately uses the same call eventkit does.

Importing this module before ib_insync makes the import safe. It must
therefore be the FIRST import in any module that touches ib_insync.
"""

import asyncio


def _ensure_policy_loop() -> None:
    try:
        asyncio.get_event_loop_policy().get_event_loop()
        return
    except (RuntimeError, DeprecationWarning):
        pass

    # A fresh loop is only ever used as the object eventkit caches at import
    # time; all real IBKR traffic runs on the dedicated loop owned by
    # ibkr_client's worker thread.
    asyncio.set_event_loop(asyncio.new_event_loop())


_ensure_policy_loop()
