"""
Simulated business datasets for the Supply Chain Disruption system.

The data lives in JSON files next to this module and is validated on load by
SupplyChainDataset (field formats + cross-references). This replaces external
supplier/logistics APIs for the academic MVP.

Expected decisions are NOT stored here; they live in tests/fixtures so that
agents can never read the "answer" for an incident.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from supply_chain.schemas import (
    InventoryRecord,
    Product,
    RouteRecord,
    Site,
    SupplierRecord,
    SupplyChainDataset,
)

DATA_DIR = Path(__file__).parent


def _read_json(name: str) -> Any:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def load_dataset(data_dir: Path = DATA_DIR) -> SupplyChainDataset:
    """Load and validate all datasets. Raises pydantic.ValidationError if inconsistent."""
    def read(name: str) -> Any:
        with open(data_dir / name, encoding="utf-8") as f:
            return json.load(f)

    catalog = read("catalog.json")
    return SupplyChainDataset(
        products=catalog["products"],
        sites=catalog["sites"],
        inventory=read("inventory.json"),
        suppliers=read("suppliers.json"),
        routes=read("routes.json"),
        incidents=read("incidents.json"),
    )


DATASET: SupplyChainDataset = load_dataset()

PRODUCTS: List[Product] = DATASET.products
SITES: List[Site] = DATASET.sites
INVENTORY: List[InventoryRecord] = DATASET.inventory
SUPPLIERS: List[SupplierRecord] = DATASET.suppliers
ROUTES: List[RouteRecord] = DATASET.routes

# Raw incident dicts (already validated above); construct with DisruptionIncident(**d).
SAMPLE_INCIDENTS: List[Dict[str, Any]] = _read_json("incidents.json")


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_inventory_record(warehouse_id: str, product_id: str) -> InventoryRecord | None:
    for rec in INVENTORY:
        if rec.warehouse_id == warehouse_id and rec.product_id == product_id:
            return rec
    return None


def get_suppliers_for_product(product_id: str) -> list[SupplierRecord]:
    return [s for s in SUPPLIERS if product_id in s.supported_products]


def get_routes_for_warehouse(warehouse_id: str) -> list[RouteRecord]:
    return [r for r in ROUTES if r.origin == warehouse_id]


def get_all_routes_to_destination(destination: str) -> list[RouteRecord]:
    return [r for r in ROUTES if r.destination == destination]


def get_all_inventory_for_product(product_id: str) -> list[InventoryRecord]:
    return [r for r in INVENTORY if r.product_id == product_id]
