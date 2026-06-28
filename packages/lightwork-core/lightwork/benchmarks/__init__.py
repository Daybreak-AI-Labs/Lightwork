"""Maverick benchmark tooling (the ``lightwork.benchmarks`` namespace).

Distinct from the repo-root ``benchmarks/`` script dir (the live GAIA /
tau2 / terminal-bench / SWE-bench harnesses): those drive a real provider
and aren't importable as ``lightwork.*``. This package holds the parts of
the eval story that are plain, importable library code -- starting with
``reproducible_v2``, the pinned-conditions reproducibility harness.
"""
