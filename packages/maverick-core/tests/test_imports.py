"""Smoke test: every kernel module imports cleanly."""
from __future__ import annotations


def test_top_level_imports():
    import lightwork
    import lightwork.agent  # noqa: F401
    import lightwork.blackboard  # noqa: F401
    import lightwork.budget  # noqa: F401
    import lightwork.cli  # noqa: F401
    import lightwork.config  # noqa: F401
    import lightwork.llm  # noqa: F401
    import lightwork.orchestrator  # noqa: F401
    import lightwork.sandbox  # noqa: F401
    import lightwork.sandbox.local  # noqa: F401
    import lightwork.server  # noqa: F401
    import lightwork.skills  # noqa: F401
    import lightwork.swarm  # noqa: F401
    import lightwork.tools  # noqa: F401
    import lightwork.tools.ask_user  # noqa: F401
    import lightwork.tools.fs  # noqa: F401
    import lightwork.tools.shell  # noqa: F401
    import lightwork.tools.spawn  # noqa: F401
    import lightwork.world_model  # noqa: F401
    assert lightwork.__version__


def test_provider_registry():
    from lightwork.providers import KNOWN_PROVIDERS, get_provider_client  # noqa: F401
    assert "anthropic" in KNOWN_PROVIDERS
    assert "openai" in KNOWN_PROVIDERS
    assert "openrouter" in KNOWN_PROVIDERS
    assert "ollama" in KNOWN_PROVIDERS


def test_sandbox_module_loads():
    # Importing docker.py should not require Docker to be installed.
    from lightwork.sandbox import DockerBackend, LocalBackend, build_sandbox  # noqa: F401
