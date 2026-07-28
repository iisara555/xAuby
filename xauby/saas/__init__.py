"""Multi-tenant control plane for hosted xAuby engines.

``create_app`` is exported lazily. Importing it eagerly pulled FastAPI — and the
whole catalog — into any process that only wanted a data module, which made
``scripts/certify_preset.py`` unable to read the preset specs it exists to
certify: building the catalog requires the certificates, and issuing a
certificate requires the specs. Deferring the import breaks that cycle and keeps
`python -c "import xauby.saas.preset_specs"` cheap.
"""
from typing import TYPE_CHECKING, Any

from xauby.saas.settings import SaaSSettings

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from xauby.saas.app import create_app

__all__ = ["SaaSSettings", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from xauby.saas.app import create_app as _create_app

        return _create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
