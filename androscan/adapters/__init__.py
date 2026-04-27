"""External-tool adapters for AndroScan.

Today this package only contains :mod:`androscan.adapters.frida_client`, the
host-side Frida bindings used by the Hook Lab (Phase 6 step 4 / DEC-023).
Future adapters that wrap other external tools (e.g. an objection or
``adb`` wrapper that needs richer state than the bare functions in
``androscan/web/device_ops.py``) live alongside it.

The Frida import is lazy at the module level to keep the ``[frida]``
extra optional for the headless test suite — see
:func:`androscan.adapters.frida_client._frida_python`.
"""

from __future__ import annotations

__all__: list[str] = []
