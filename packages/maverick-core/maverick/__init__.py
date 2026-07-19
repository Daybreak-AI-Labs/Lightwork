"""Lightwork: recursive multi-agent swarm for long-horizon work."""

import os

__version__ = "0.1.7"

_LIGHTWORK_PREFIX = "LIGHTWORK_"
_MAVERICK_PREFIX = "MAVERICK_"


def _mirror_lightwork_env() -> None:
    """Mirror ``LIGHTWORK_*`` env vars onto their ``MAVERICK_*`` twins.

    Product-name compatibility shim: every ``MAVERICK_X`` setting can also
    be spelled ``LIGHTWORK_X``. ``setdefault`` means an explicitly-set
    ``MAVERICK_X`` always wins -- the mirror never overwrites, so the call
    is idempotent. Guarded so it can never break an import.
    """
    try:
        for key, value in list(os.environ.items()):
            if key.startswith(_LIGHTWORK_PREFIX):
                os.environ.setdefault(
                    _MAVERICK_PREFIX + key[len(_LIGHTWORK_PREFIX):], value
                )
    except Exception:
        pass  # never let env quirks break `import maverick`


_mirror_lightwork_env()
