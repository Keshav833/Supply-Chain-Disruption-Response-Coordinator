"""Inventory Agent tools: get_inventory, calculate_stock_shortage."""
from __future__ import annotations

from typing import Optional

from supply_chain.schemas import StockLocation, SupplyChainDataset
from supply_chain.tools.common import (
    PRODUCT,
    WAREHOUSE,
    InventoryLookup,
    StockShortage,
    dataset_or_default,
    require_id,
    require_int,
)


def get_inventory(product_id: str, warehouse_id: Optional[str] = None,
                  dataset: Optional[SupplyChainDataset] = None) -> InventoryLookup:
    """Stock records for a product, at one warehouse or (warehouse_id=None) all warehouses.

    A warehouse with no record yields an empty list; the caller must not treat that as zero stock
    for the affected warehouse (that is missing evidence)."""
    require_id("product_id", product_id, PRODUCT)
    if warehouse_id is not None:
        require_id("warehouse_id", warehouse_id, WAREHOUSE)
    ds = dataset_or_default(dataset)
    records = [
        StockLocation(warehouse_id=r.warehouse_id, available_stock=r.available_stock,
                      safety_stock=r.safety_stock, usable_stock=r.usable_stock)
        for r in ds.inventory
        if r.product_id == product_id and (warehouse_id is None or r.warehouse_id == warehouse_id)
    ]
    records.sort(key=lambda s: (-s.usable_stock, s.warehouse_id))
    return InventoryLookup(product_id=product_id, warehouse_id=warehouse_id, records=records)


def calculate_stock_shortage(available_stock: int, safety_stock: int,
                             required_quantity: int) -> StockShortage:
    """usable = max(0, available - safety); replenishment = max(0, required + safety - available)."""
    require_int("available_stock", available_stock, 0)
    require_int("safety_stock", safety_stock, 0)
    require_int("required_quantity", required_quantity, 1)
    usable = max(0, available_stock - safety_stock)
    shortage = required_quantity + safety_stock - available_stock
    return StockShortage(
        available_stock=available_stock, safety_stock=safety_stock, required_quantity=required_quantity,
        usable_stock=usable, shortage=shortage, replenishment_required=max(0, shortage),
        safety_stock_met=available_stock >= safety_stock, can_fulfil_from_stock=usable >= required_quantity,
    )
