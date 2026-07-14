import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


def _load(name: str):
    p = Path(__file__).parent / name
    spec = importlib.util.spec_from_file_location(f"benchmarks_{p.stem}", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


dgm_uplift = _load("dgm_uplift.py")


def test_run_solver_exposes_only_isolated_instance_repo(tmp_path, monkeypatch):
    solver_dir = tmp_path / "solver"
    solver_dir.mkdir()
    (solver_dir / "solver.py").write_text(
        "from pathlib import Path\n"
        "def solve(instance):\n"
        "    Path(instance.repo_path, 'tampered').write_text('x', encoding='utf-8')\n"
        "    return ''\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    inst = SimpleNamespace(
        instance_id="inst-1",
        repo_path=repo,
        fail_to_pass=[],
        pass_to_pass=[],
        gold_patch="",
        brief="",
        language="python",
        total_tests=0,
    )
    monkeypatch.setattr(dgm_uplift, "score_instance", lambda *args, **kwargs: False)

    assert dgm_uplift.run_solver(solver_dir, [inst], tmp_path / "work", timeout=10) == {
        "inst-1": False
    }

    assert not (repo / "tampered").exists()
    assert list((tmp_path / "work").rglob("tampered"))
