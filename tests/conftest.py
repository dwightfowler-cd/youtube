import re

import pytest
from helpers import REAL_CONFIG_DIR, server_env


@pytest.fixture(scope="session")
def real_key() -> str:
    """The real API key. A missing key is a failure, not a skip: nothing public can run."""
    f = REAL_CONFIG_DIR / "api_key"
    assert f.exists(), f"Create {f} (mode 600) with your API key to run the real-API tests"
    text = f.read_text()
    m = re.search(r"AIza[0-9A-Za-z_\-]{30,}", text)
    assert m, f"No API key found in {f}"
    return m.group(0)


@pytest.fixture
def env(tmp_path, real_key):
    """Real config (real key), throwaway cache so tests never touch your real cache."""
    return server_env(cache_home=tmp_path / "cache")


@pytest.fixture(scope="session")
def real_token_path():
    p = REAL_CONFIG_DIR / "token.json"
    if not p.exists():
        pytest.skip(f"NOT TESTED: no OAuth token at {p}. Run `uv run youtube auth` first.")
    assert oct(p.stat().st_mode & 0o777) == "0o600", "token.json must be mode 600"
    return p
