"""Repo-root pytest bootstrap: isolate ``~/.maverick`` BEFORE any maverick import.

Many maverick modules freeze ``Path.home()``-derived paths into module-level
constants at import time (``world_model.DEFAULT_DB``, ``skills.SKILLS_DIR``,
``skill_stats.DEFAULT_PATH``, ``self_learning.LEARNED_PATH``, the audit dirs,
...). The per-test ``HOME`` monkeypatch in maverick-core's conftest runs too
late for those: in a full-suite run the first collected module to import
maverick bakes the REAL home into the constants, and every test that then
relies on a default path writes the developer's actual ``~/.maverick`` --
observed as fake swe-bench goals + phantom spend in ``maverick budget``, junk
``good``/``bad`` skills that ``relevant_skills()`` would inject into REAL
future runs, and test rows in the real audit log.

Mutating the environment at root-conftest *import* is the earliest hook pytest
offers: it precedes package conftests and every collection import, so the
frozen constants bake a throwaway session dir instead. It also covers
``benchmarks/`` (no conftest of its own) and ``apps/``. Per-test ``tmp_path``
HOME fixtures still apply on top for call-time resolution, and subprocesses
spawned by tests inherit the redirect.

``test_home_isolation.py`` pins this contract against the OS-level home (via
``pwd``), which environment variables cannot fool.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import types

import pytest

_session_home = tempfile.mkdtemp(prefix="maverick-test-home-")
os.environ["HOME"] = _session_home
os.environ["USERPROFILE"] = _session_home  # Windows: what Path.home() reads
atexit.register(shutil.rmtree, _session_home, True)

# Secure-by-default ships ON in production (audit signing, at-rest encryption,
# fail-closed high-risk consent, ...). The existing suite asserts each control's
# on/off mechanics under explicit config, so pin the legacy posture here for
# stability (same pattern as the MAVERICK_BUILTIN_SKILLS=0 pin); the secure
# DEFAULT itself is covered by test_secure_defaults.py, which overrides this.
os.environ.setdefault("MAVERICK_SECURE_DEFAULT", "0")

# Voice STT model auto-fetch requires explicit opt-in. A test that wanders
# into the local STT chain must never inherit an opt-in and download a
# ~150 MB Whisper model from Hugging Face. Tests of the auto-fetch mechanics
# set this env themselves (against a mocked model download transport).
os.environ.setdefault("MAVERICK_VOICE_AUTO_FETCH", "0")


def pytest_addoption(parser):
    parser.addoption(
        "--shard", default=None, metavar="INDEX/COUNT",
        help="run only the test files in slice INDEX (1-based) of COUNT: files "
             "are dealt round-robin in collection order, so the slices are "
             "deterministic, disjoint and together cover the whole suite. CI "
             "runs three slices per interpreter on two-core runners.",
    )


def pytest_collection_modifyitems(config, items):
    """Keep one file-level slice of the collection when ``--shard`` is given.

    File-level (not test-level) so module-scoped fixtures, module-level
    TestClients and ``--dist loadfile`` keep their meaning inside a slice; the
    same dealing happens in every xdist worker, so a slice is consistent
    across workers and across interpreters.
    """
    spec = config.getoption("--shard")
    if not spec:
        return
    try:
        index, count = (int(part) for part in spec.split("/", 1))
    except ValueError as exc:
        raise pytest.UsageError(f"--shard expects INDEX/COUNT, got {spec!r}") from exc
    if count < 1 or not 1 <= index <= count:
        raise pytest.UsageError(f"--shard {spec}: INDEX must be within 1..COUNT")
    slot: dict[str, int] = {}
    for item in items:
        slot.setdefault(str(item.path), len(slot))
    keep = [item for item in items if slot[str(item.path)] % count == index - 1]
    drop = [item for item in items if slot[str(item.path)] % count != index - 1]
    items[:] = keep
    if drop:
        config.hook.pytest_deselected(items=drop)


@pytest.fixture(autouse=True)
def _isolate_dynamic_maverick_home(tmp_path, monkeypatch):
    """Give every package a fresh dynamic home, including on Windows.

    The core test package already did this locally, but dashboard/channels/MCP
    tests share the repo-root session home. Stores migrated to the documented
    ``MAVERICK_HOME`` resolver then leaked RBAC grants, invites, and UI policy
    across tests. Set every supported home input consistently per test; an
    individual test can still override any of them afterward.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    # Import-time path constants do not follow the per-test home. Re-point
    # the one the suite writes through from several packages: the runtime
    # overrides file carries the admin model allow-list and per-role model
    # overrides, so a test that set an allow-list without patching
    # OVERRIDES_PATH wrote the shared session home, and any later test on the
    # same worker resolving a model was silently capped to it (seen as the
    # cost-routing test returning the older Opus under xdist, #2410).
    try:
        from maverick import runtime_overrides as _runtime_overrides
    except Exception:  # pragma: no cover -- core not installed in this env
        pass
    else:
        monkeypatch.setattr(
            _runtime_overrides, "OVERRIDES_PATH",
            tmp_path / ".maverick" / "runtime-overrides.toml", raising=False,
        )


@pytest.fixture(autouse=True)
def _isolate_runner_shutdown_admission(monkeypatch):
    """A prior TestClient shutdown must not close admission in another test."""
    import sys

    runner = sys.modules.get("maverick.runner")
    if runner is not None:
        monkeypatch.setattr(runner, "_accepting_goals", True)


@pytest.fixture(autouse=True)
def _restore_environ():
    """Put ``os.environ`` back the way the test found it.

    ``monkeypatch`` restores what a test set through it, but not what the code
    under test sets: the config loader mirrors a wizard-written ``.env`` into
    the environment with ``setdefault`` (``ANTHROPIC_API_KEY``), the
    ``LIGHTWORK_*`` alias layer mirrors into ``MAVERICK_*``, the moat preflight
    sets ``MAVERICK_SUPPRESS_SANDBOX_WARNING``. Each of those stayed set for
    every later test in the worker, and pytest-xdist's size-first file order
    made "later" mean any file at all (#2410). Restoring the snapshot keeps
    each test's environment its own without making every test pre-record the
    keys its code under test might mirror.
    """
    snapshot = dict(os.environ)
    yield
    for key in set(os.environ) - set(snapshot):
        del os.environ[key]
    for key, value in snapshot.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _fail_the_test_that_forks_a_module():
    """Fail the test that leaves a package attribute pointing at a module copy.

    A test that pops ``maverick.plugins`` from ``sys.modules``, triggers a
    re-import, then puts the ORIGINAL entry back leaves two live copies of the
    module: ``sys.modules`` holds the original, the ``maverick`` package
    attribute holds the copy. From then on every string-form monkeypatch in
    the process (``monkeypatch.setattr("maverick.plugins.x", ...)`` resolves
    through the attribute) patches the copy while call-time imports in the
    code under test get the original -- a leak that only shows once files
    run in a different order, i.e. under pytest-xdist, and then in whichever
    unrelated file happens to follow (nine victims across four files in
    #2410). Checking after every test names the culprit instead.
    """
    yield
    forked = []
    for name, module in list(sys.modules.items()):
        if module is None or "." not in name or not name.startswith("maverick"):
            continue
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is None:
            continue
        bound = getattr(parent, child, None)
        if isinstance(bound, types.ModuleType) and bound is not module:
            forked.append(name)
    if forked:
        pytest.fail(
            "this test left a package attribute bound to a different module object "
            f"than sys.modules holds for: {sorted(forked)}. Do not pop maverick "
            "modules and re-import them; block the import with "
            "monkeypatch.setitem(sys.modules, name, None) or patch the attribute.",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _reset_governed_process_authority_pin():
    """Keep process-lifetime production admission state isolated per test."""
    try:
        from maverick import governed_records
    except ImportError:
        yield
        return
    governed_records._reset_process_authority_pin_for_testing()
    try:
        yield
    finally:
        governed_records._reset_process_authority_pin_for_testing()


@pytest.fixture(autouse=True)
def _reset_provider_health():
    """Start every test with an empty provider-health ledger.

    ``provider_health.get()`` is a process-wide singleton the cost router
    consults on every pick: a provider/model with a high recent error rate is
    excluded from routing. A test that records failures against a real model
    and does not reset leaves that exclusion in place for every later test in
    the worker -- seen as the cost-routing test resolving the orchestrator to
    the previous Opus generation once xdist's file order put such a test
    ahead of it (#2410).
    """
    def _reset() -> None:
        try:
            from maverick import provider_health
        except ImportError:
            return
        try:
            provider_health.get().reset()
        except Exception:  # pragma: no cover -- isolation never blocks a test
            pass

    _reset()
    try:
        yield
    finally:
        _reset()


@pytest.fixture(autouse=True)
def _isolate_default_audit_writers():
    """Do not let a cached deployment-global writer cross test data roots.

    ``default_audit_log`` intentionally caches one writer for a production
    process. Tests change ``MAVERICK_HOME`` for every case, though, and the
    process-level singleton would otherwise keep writing to the first case's
    audit chain. Besides leaking state, stable outbox identities from two
    independent test worlds can then collide and correctly fail closed.
    """
    try:
        from maverick.audit import writer
    except ImportError:
        yield
        return

    def _reset() -> None:
        with writer._default_lock:
            writer._default = None
            writer._defaults.clear()

    _reset()
    try:
        yield
    finally:
        _reset()


@pytest.fixture(autouse=True)
def _reset_cluster_halt_cache():
    """Forget the killswitch's cached cluster-wide halt after every test.

    ``killswitch._shared_halt_active`` serves its last answer for up to two
    seconds without re-reading the backend. A test that armed or observed a
    cluster halt therefore halted whichever test the same worker ran next --
    whole files failing with "DID NOT RAISE" when ``run_goal`` stopped cleanly
    instead of raising. Every package runs agents, so the reset lives here.
    """
    yield
    try:
        from maverick import killswitch
        killswitch._last_shared_check_ts = 0.0
        killswitch._last_shared_halt = None
        world, killswitch._shared_world = killswitch._shared_world, None
        if world is not None and hasattr(world, "close"):
            world.close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_channel_execution_authority(request, monkeypatch, tmp_path):
    """Channels import the default world path once; each test owns a new DB.

    Keep this in the root conftest: both core and channels have a ``tests``
    package, which otherwise collides during combined importlib collection.
    """
    if "maverick-channels" not in request.node.path.parts:
        return
    try:
        from maverick import world_model
    except ImportError:
        return  # Channels-only environments may intentionally omit the core.
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "channel-world.db")


@pytest.fixture
def fresh_human_decision(monkeypatch):
    """Stand in for the fresh human decision an exact-operation permit needs.

    The shell and clipboard tools pass ``allow_auto_approve=False``, so
    ``MAVERICK_CONSENT_MODE=auto-approve`` no longer grants them. Tests whose
    subject is the tool rather than the gate use this fixture to supply one
    reviewed decision through the production card and one-use permit path; the
    consent-gate suites keep exercising denial explicitly.
    """
    import time

    import maverick.safety.consent as consent

    def decide(
        action,
        *,
        risk="medium",
        scope=None,
        detail=None,
        provenance=None,
        raise_on_deny=False,
        allow_auto_approve=True,
        consult_ledger=True,
        approval_card=None,
    ):
        del scope, detail, provenance, raise_on_deny
        del allow_auto_approve, consult_ledger, approval_card
        return consent.ConsentDecision(
            True, "dashboard", risk, time.time(), actor="user:reviewer",
        )

    monkeypatch.setattr(consent, "require_consent", decide)
