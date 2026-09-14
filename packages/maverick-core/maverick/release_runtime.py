"""Offline checks for dependencies included in distributable release artifacts.

These run in the frozen executable during staging and installed acceptance.
They use an in-memory database and a private metrics registry, never customer
credentials or a remote database. Live account acceptance remains separate.
"""
from __future__ import annotations

import importlib
import xml.etree.ElementTree as ET


def verify_release_database_runtime() -> None:
    """Exercise SQL execution, the selected PostgreSQL dialect and bundled libpq."""
    sqlalchemy = importlib.import_module("sqlalchemy")
    pq = importlib.import_module("psycopg.pq")
    pool_module = importlib.import_module("psycopg_pool")
    if pq.__impl__ != "binary" or pq.version() <= 0:
        raise RuntimeError("release PostgreSQL driver requires bundled binary libpq")

    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(sqlalchemy.text("CREATE TABLE probe (value INTEGER)"))
            connection.execute(sqlalchemy.text("INSERT INTO probe VALUES (:value)"),
                               {"value": 42})
        with engine.connect() as connection:
            if connection.execute(sqlalchemy.text("SELECT value FROM probe")).scalar() != 42:
                raise RuntimeError("release SQL query returned an unexpected result")
    finally:
        engine.dispose()

    # Engine construction loads the actual URL-selected DBAPI without opening
    # a socket. The closed pool also checks the platform storage dependency.
    engine = sqlalchemy.create_engine("postgresql+psycopg://127.0.0.1:9/release_check")
    try:
        if engine.dialect.driver != "psycopg":
            raise RuntimeError("release PostgreSQL dialect is unavailable")
        pool = pool_module.ConnectionPool(
            "host=127.0.0.1 port=9 dbname=release_check", min_size=0, max_size=1,
            open=False,
        )
        pool.close()
    finally:
        engine.dispose()


def verify_release_mathml_runtime() -> None:
    """Exercise the real converter, packaged symbol data and malformed input."""
    from .tools.latex_tool import to_mathml

    root = ET.fromstring(to_mathml(r"\frac{\alpha}{2}"))
    fraction = root.find(".//{http://www.w3.org/1998/Math/MathML}mfrac")
    if fraction is None or len(fraction) != 2 or "α" not in "".join(root.itertext()):
        raise RuntimeError("release MathML conversion returned incomplete output")
    try:
        to_mathml(r"\frac{1}")
    except RuntimeError:
        return
    raise RuntimeError("release MathML converter accepted an incomplete fraction")


def verify_release_metrics_runtime() -> None:
    """Render the platform's real collectors without opening a metrics listener."""
    from .observability import _register_computed_collector

    prometheus = importlib.import_module("prometheus_client")
    registry = prometheus.CollectorRegistry()
    _register_computed_collector(registry=registry)
    rendered = prometheus.generate_latest(registry).decode("utf-8")
    for name in ("maverick_tool_latency_ms", "maverick_provider_error_rate",
                 "maverick_tool_latency_budget_breaches"):
        if f"# HELP {name} " not in rendered:
            raise RuntimeError(f"release metrics collector did not export {name}")
