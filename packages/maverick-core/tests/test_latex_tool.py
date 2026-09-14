"""LaTeX tool (ROADMAP 2027 H2)."""
from __future__ import annotations

import shlex
from pathlib import Path

import pytest
from maverick.tools import latex_tool as latex_module
from maverick.tools.latex_tool import latex_tool, to_mathml


class _FakeSandbox:
    """Sandbox whose exec creates the TeX engine's output PDF, then succeeds.

    ``workdir`` is the confinement root the tool resolves ``out`` against."""

    def __init__(self, workdir=".", succeed=True):
        self.workdir = str(workdir)
        self.succeed = succeed

    def exec(self, cmd, timeout=None):
        if self.succeed:
            # tectonic --outdir <dir> <tex>  /  pdflatex -output-directory <dir>
            parts = shlex.split(cmd)
            outdir = None
            for flag in ("--outdir", "-output-directory"):
                if flag in parts:
                    outdir = parts[parts.index(flag) + 1]
            if outdir:
                Path(outdir).mkdir(parents=True, exist_ok=True)
                (Path(outdir) / "doc.pdf").write_bytes(b"%PDF-1.4 fake")

        class R:
            pass
        r = R()
        r.exit_code = 0 if self.succeed else 127
        r.stdout, r.stderr = "", "" if self.succeed else "command not found"
        return r


def test_mathml_conversion():
    pytest.importorskip("latex2mathml")
    out = to_mathml(r"x^2 + 1")
    assert "<math" in out and "</math>" in out


def test_mathml_op_via_tool():
    pytest.importorskip("latex2mathml")
    out = latex_tool(_FakeSandbox()).fn({"op": "mathml", "latex": r"\frac{a}{b}"})
    assert "<math" in out


def test_render_success(tmp_path):
    # `out` is workspace-relative; the PDF lands under the sandbox workdir.
    tool = latex_tool(_FakeSandbox(tmp_path, succeed=True))
    out = tool.fn({"op": "render", "latex": r"\documentclass{article}\begin{document}hi\end{document}",
                   "out": "out/paper.pdf"})
    assert "rendered PDF" in out
    assert (tmp_path / "out" / "paper.pdf").exists()


def test_render_engine_missing(tmp_path):
    tool = latex_tool(_FakeSandbox(tmp_path, succeed=False))
    out = tool.fn({"op": "render", "latex": r"\documentclass{article}\begin{document}x\end{document}",
                   "out": "x.pdf"})
    assert out.startswith("ERROR") and "installed" in out


def test_empty_latex():
    assert latex_tool(_FakeSandbox()).fn({"op": "mathml", "latex": "  "}).startswith("ERROR")


def test_render_out_escaping_workspace_is_rejected(tmp_path):
    # A model-supplied absolute/`..` `out` must not write outside the sandbox.
    tool = latex_tool(_FakeSandbox(tmp_path, succeed=True))
    out = tool.fn({"op": "render",
                   "latex": r"\documentclass{article}\begin{document}hi\end{document}",
                   "out": "../escaped.pdf"})
    assert out.startswith("ERROR") and "escape" in out.lower()
    assert not (tmp_path.parent / "escaped.pdf").exists()


def test_non_string_latex_does_not_crash(tmp_path):
    fn = latex_tool(_FakeSandbox(tmp_path)).fn
    assert fn({"op": "mathml", "latex": 5}).startswith("ERROR")
    assert fn({"op": "mathml", "latex": [1, 2]}).startswith("ERROR")


@pytest.mark.parametrize("op", ["mathml", "render"])
@pytest.mark.parametrize("source", [5, [1, 2], {"latex": "x"}, True])
def test_invalid_source_is_refused_before_conversion(monkeypatch, tmp_path, op, source):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid source reached the converter or document engine")

    monkeypatch.setattr(latex_module, "to_mathml", unexpected)
    monkeypatch.setattr(latex_module, "sandbox_run", unexpected)
    out = latex_tool(_FakeSandbox(tmp_path)).fn({"op": op, "latex": source})
    assert out.startswith("ERROR") and "string" in out


@pytest.mark.parametrize("source", [r"\frac{1}", "x^", r"\sqrt{"])
def test_malformed_math_returns_actionable_error(source, tmp_path):
    pytest.importorskip("latex2mathml")
    out = latex_tool(_FakeSandbox(tmp_path)).fn({"op": "mathml", "latex": source})
    assert out.startswith("ERROR") and "LaTeX" in out


@pytest.mark.parametrize("outcome", ["success", "engine-error", "io-error"])
def test_render_cleans_private_scratch_on_every_outcome(monkeypatch, tmp_path, outcome):
    paths = []

    def engine(sandbox, argv, timeout):
        tex = Path(argv[-1])
        paths.append(tex.parent)
        assert tex.read_text(encoding="utf-8") == "private customer document"
        if outcome == "io-error":
            raise OSError("temporary output storage unavailable")
        if outcome == "engine-error":
            return 127, "", "engine unavailable"
        tex.with_suffix(".pdf").write_bytes(b"%PDF-1.4 fixture")
        return 0, "", ""

    monkeypatch.setattr(latex_module, "sandbox_run", engine)
    dest = tmp_path / "out.pdf"
    dest.write_bytes(b"prior reviewed report")
    out = latex_tool(_FakeSandbox(tmp_path)).fn({
        "op": "render", "latex": "private customer document", "out": "out.pdf",
    })
    assert paths and all(not path.exists() for path in paths)
    if outcome == "success":
        assert out.startswith("rendered PDF")
        assert dest.read_bytes() == b"%PDF-1.4 fixture"
    else:
        assert out.startswith("ERROR")
        assert dest.read_bytes() == b"prior reviewed report"


def test_invalid_destination_is_refused_before_engine(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid destination reached the document engine")

    monkeypatch.setattr(latex_module, "sandbox_run", unexpected)
    out = latex_tool(_FakeSandbox(tmp_path)).fn({
        "op": "render", "latex": "customer document", "out": "../escaped.pdf",
    })
    assert out.startswith("ERROR") and "escape" in out.lower()
