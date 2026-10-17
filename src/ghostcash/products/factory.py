"""Store factory - mirrors ai/factory.py and publishing/factory.py's
pattern so the rest of the system stays decoupled from a specific
store vendor (CLAUDE.md section 26)."""

from __future__ import annotations

from ghostcash.config.schema import Config
from ghostcash.products.base import Store, StoreError
from ghostcash.products.gumroad import GumroadStore


def get_store(config: Config) -> Store:
    provider_name = (config.products.store_provider or "").strip().lower()
    if provider_name == "gumroad":
        return GumroadStore(api_key=config.gumroad_api_key)
    raise StoreError(
        f"Unknown products.store_provider: {provider_name!r}. Currently implemented: gumroad."
    )
