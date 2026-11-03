"""Explicitly opt the demo protocol tests into an isolated mock tenant."""
import pytest


@pytest.fixture(scope="module", autouse=True)
def _enable_test_tenant():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PIA_ENABLE_ONETRUST_MOCK", "1")
        patch.setenv("ONETRUST_TOKEN", "demo-bearer-token")
        yield
