"""Apply the shared offline network guard to the skill test suite.

The plugin test root lives outside ``tests/``, so it reuses the same fixture
implementation instead of keeping a second copy of the rule.
"""

import importlib.util
from pathlib import Path

_GUARD = Path(__file__).resolve().parents[3] / "tests" / "conftest.py"
_spec = importlib.util.spec_from_file_location("research_session_offline_guard", _GUARD)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

offline_network_guard = _module.offline_network_guard
