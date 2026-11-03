"""Vision calls use the run budget and configured model, within the workspace."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from maverick.budget import Budget, BudgetExceeded
from maverick.tools import ToolRegistry
from maverick.tools.view_image import view_image


def test_view_image_passes_budget_and_uses_operator_model(tmp_path, monkeypatch):
    from maverick.llm import LLM

    (tmp_path / "image.png").write_bytes(b"image")
    monkeypatch.setenv("MAVERICK_VISION_MODEL", "configured:vision")
    seen = {}
    def init(self, model=None):
        seen["model"] = model
    def complete(self, **kwargs):
        seen["budget"] = kwargs["budget"]
        return SimpleNamespace(text="a picture")
    monkeypatch.setattr(LLM, "__init__", init)
    monkeypatch.setattr(LLM, "complete", complete)
    budget = Budget()
    tool = view_image(SimpleNamespace(workdir=tmp_path), budget=budget)
    assert tool.fn({"source": "image.png"}) == "a picture"
    assert seen == {"model": "configured:vision", "budget": budget}
    assert "model" not in tool.input_schema["properties"]
    assert "configured by the operator" in tool.fn({"source": "image.png", "model": "attacker:model"})
    assert "active run budget" in view_image(SimpleNamespace(workdir=tmp_path)).fn({"source": "image.png"})


def test_view_image_propagates_budget_stop_through_registry(tmp_path, monkeypatch):
    from maverick.llm import LLM

    (tmp_path / "image.png").write_bytes(b"image")
    monkeypatch.setattr(LLM, "__init__", lambda self, model=None: None)
    def stop(self, **kwargs):
        raise BudgetExceeded("run budget exhausted")
    monkeypatch.setattr(LLM, "complete", stop)
    registry = ToolRegistry()
    registry.register(view_image(SimpleNamespace(workdir=tmp_path), budget=Budget()))
    with pytest.raises(BudgetExceeded, match="run budget exhausted"):
        asyncio.run(registry.run("view_image", {"source": "image.png"}))


def test_view_image_confines_local_source_to_task_workspace(tmp_path, monkeypatch):
    from maverick.tools.view_image import _load_image

    workspace = tmp_path / "task"
    workspace.mkdir()
    (workspace / "inside.png").write_bytes(b"inside")
    (tmp_path / "outside.png").write_bytes(b"outside")
    monkeypatch.chdir(tmp_path)
    sandbox = SimpleNamespace(workdir=workspace)
    assert _load_image("inside.png", sandbox)[0] == b"inside"
    assert _load_image("../outside.png", sandbox) is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are unavailable")
def test_view_image_rejects_fifo_without_blocking(tmp_path):
    from maverick.tools.view_image import _load_image

    os.mkfifo(tmp_path / "image.png")
    assert _load_image("image.png", SimpleNamespace(workdir=tmp_path)) is None
