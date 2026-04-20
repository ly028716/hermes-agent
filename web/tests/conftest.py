"""
Pytest configuration for web/tests directory.
"""
import os
import sys
import pytest

# Add project root to Python path for imports
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Isolate tests by setting a temporary HERMES_HOME."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    yield
