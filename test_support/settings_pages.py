"""Settings > All settings with every group's settings in it.

The page arrives with the list of groups and loads a group's settings when it
is opened (``/settings/all?group=<slug>`` renders one group). ``every_group``
returns each group's page joined, for a test that reads every setting's row.
"""

from __future__ import annotations


def every_group(client, **kwargs) -> str:
    from maverick_dashboard import settings_catalog
    from maverick_dashboard.settings_all_page import _slug

    pages = []
    for name in settings_catalog.GROUPS:
        response = client.get("/settings/all", params={"group": _slug(name)}, **kwargs)
        assert response.status_code == 200, (name, response.status_code)
        pages.append(response.text)
    return "\n".join(pages)
