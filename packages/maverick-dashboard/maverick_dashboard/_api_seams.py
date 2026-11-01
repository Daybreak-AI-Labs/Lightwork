"""Late-bound access to names on the ``maverick_dashboard.api`` module.

Tests patch auth helpers, the world accessor and similar names on the api
module (``monkeypatch.setattr(api, "require_permission", ...)``).  Router
modules split out of api.py import these wrappers instead of the originals,
so each call resolves the api module's current attribute and the patch
applies exactly as it did when the handler lived in api.py.  Classes and
schemas are imported from their origins; only callables go through here.
"""
from __future__ import annotations

import importlib
from typing import Any


def _api() -> Any:
    return importlib.import_module("maverick_dashboard.api")


def _dashboard_token_authenticated(*args: Any, **kwargs: Any) -> Any:
    return _api()._dashboard_token_authenticated(*args, **kwargs)


def _event_trigger_allowed_owners(*args: Any, **kwargs: Any) -> Any:
    return _api()._event_trigger_allowed_owners(*args, **kwargs)


def _live_tool_index(*args: Any, **kwargs: Any) -> Any:
    return _api()._live_tool_index(*args, **kwargs)


def _live_tool_schemas(*args: Any, **kwargs: Any) -> Any:
    return _api()._live_tool_schemas(*args, **kwargs)


def _supervisor(*args: Any, **kwargs: Any) -> Any:
    return _api()._supervisor(*args, **kwargs)


def auth_genuinely_off(*args: Any, **kwargs: Any) -> Any:
    return _api().auth_genuinely_off(*args, **kwargs)


def caller_principal(*args: Any, **kwargs: Any) -> Any:
    return _api().caller_principal(*args, **kwargs)


def caller_suites(*args: Any, **kwargs: Any) -> Any:
    return _api().caller_suites(*args, **kwargs)


def configured_goal_ceiling_dollars(*args: Any, **kwargs: Any) -> Any:
    return _api().configured_goal_ceiling_dollars(*args, **kwargs)


def execution_user_id_from_request(*args: Any, **kwargs: Any) -> Any:
    return _api().execution_user_id_from_request(*args, **kwargs)


def goal_owner_filter(*args: Any, **kwargs: Any) -> Any:
    return _api().goal_owner_filter(*args, **kwargs)


def require_permission(*args: Any, **kwargs: Any) -> Any:
    return _api().require_permission(*args, **kwargs)


def run_in_threadpool(*args: Any, **kwargs: Any) -> Any:
    return _api().run_in_threadpool(*args, **kwargs)
