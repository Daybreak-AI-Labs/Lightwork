"""Focused regressions for the Flow authoring accessibility contracts."""

from __future__ import annotations

from pathlib import Path

TEMPLATES = Path(__file__).parents[1] / "maverick_dashboard" / "templates"


def _source(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_qa_064_flow_designer_keyboard_selection_movement_and_pan() -> None:
    source = _source("flow_designer.js")
    node_handler = source[
        source.index("gNodes.addEventListener('keydown'") :
        source.index("canvas.addEventListener('keydown'")
    ]
    canvas_handler = source[
        source.index("canvas.addEventListener('keydown'") :
        source.index("function deleteEdge")
    ]

    assert "evt.key === 'Enter' || evt.key === ' '" in node_handler
    assert "selectNode(id); return" in node_handler
    assert "var step = evt.shiftKey ? 48 : 16" in node_handler
    for key, movement in (
        ("ArrowLeft", "dx = -step"),
        ("ArrowRight", "dx = step"),
        ("ArrowUp", "dy = -step"),
        ("ArrowDown", "dy = step"),
    ):
        assert f"evt.key === '{key}'" in node_handler
        assert movement in node_handler
    assert "pushHistory(); selected = id; multiSel = {}" in node_handler
    assert "n.x += dx; n.y += dy" in node_handler

    for key, movement in (
        ("ArrowLeft", "view.x += 32"),
        ("ArrowRight", "view.x -= 32"),
        ("ArrowUp", "view.y += 32"),
        ("ArrowDown", "view.y -= 32"),
    ):
        assert f"evt.key === '{key}'" in canvas_handler
        assert movement in canvas_handler
    assert "if (handled) { evt.preventDefault(); render(); }" in canvas_handler


def test_qa_064_flow_designer_keyboard_delete_and_escape_are_context_safe() -> None:
    source = _source("flow_designer.js")
    keyboard = source[
        source.index("// keyboard: Delete removes") : source.index("buildExamples();")
    ]

    assert "var typing = tag === 'INPUT' || tag === 'TEXTAREA'" in keyboard
    assert "e.key === 'Escape' && !typing" in keyboard
    assert "selected = null; selectedEdge = null; multiSel = {}" in keyboard
    assert "e.key === 'Delete' || e.key === 'Backspace'" in keyboard
    assert "if (selectedEdge) { e.preventDefault(); deleteEdge(selectedEdge); }" in keyboard
    assert "Object.keys(multiSel).length" in keyboard
    assert "deleteSelection()" in keyboard
    assert "deleteNode(selected)" in keyboard


def test_qa_065_generated_property_condition_and_approval_controls_are_named() -> None:
    source = _source("flow_designer.js")
    generic_field = source[
        source.index("function field(label, inputEl)") :
        source.index("function input(val, oninput, ph)")
    ]
    condition = source[
        source.index("function conditionField(n, label, placeholder)") :
        source.index("// a datalist of the keys this node can see")
    ]
    approval_start = source.index("// form fields collected on sign-off")
    approval = source[
        approval_start : source.index("if (n.kind === 'delay')", approval_start)
    ]

    assert "l.id = labelId" in generic_field
    assert "inputEl.setAttribute('aria-labelledby', labelId)" in generic_field
    assert "l.htmlFor = inputEl.id" in generic_field
    assert "ta.setAttribute('aria-labelledby', conditionLabelId)" in condition
    assert "keyIn.setAttribute('aria-label', label + ' data key')" in condition
    assert "opSel.setAttribute('aria-label', label + ' operator')" in condition
    assert "valIn.setAttribute('aria-label', label + ' comparison value')" in condition
    assert (
        "nm.setAttribute('aria-label', 'Approval field ' + (fi + 1) + ' key')"
        in approval
    )
    assert (
        "lb.setAttribute('aria-label', 'Approval field ' + (fi + 1) + ' prompt')"
        in approval
    )


def test_qa_066_workflow_picker_traps_escape_tab_and_restores_opener() -> None:
    source = _source("workflow_builder.html")
    picker = source[
        source.index("var pickerOpener = null") :
        source.index("// ---- shared draft action")
    ]
    close_picker = picker[
        picker.index("function closePicker()") :
        picker.index("function pickerFocusables()")
    ]

    assert "pickerOpener = document.activeElement" in picker
    assert "tpSearch.focus()" in picker
    assert "if (e.key === 'Escape') { e.preventDefault(); closePicker(); return; }" in picker
    assert "if (e.key !== 'Tab') return" in picker
    assert "document.activeElement === first" in picker
    assert "e.preventDefault(); last.focus()" in picker
    assert "document.activeElement === last" in picker
    assert "e.preventDefault(); first.focus()" in picker
    assert "pickerOpener && pickerOpener.isConnected" in close_picker
    assert "typeof pickerOpener.focus === 'function'" in close_picker
    assert "pickerOpener.focus()" in close_picker
    assert close_picker.index("pickerOpener.focus()") < close_picker.index(
        "pickerOpener = null"
    )
