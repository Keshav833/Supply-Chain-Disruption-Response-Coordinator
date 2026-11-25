"""Supplier Agent tools: find_alternative_suppliers, check_supplier_capacity."""
from __future__ import annotations

from typing import List, Optional

from supply_chain.schemas import SupplierCapacityCheck, SupplyChainDataset
from supply_chain.tools.common import (
    PRODUCT,
    CapacityReport,
    SUPPLIER,
    RejectedOption,
    SupplierSearch,
    ToolInputError,
    dataset_or_default,
    require_id,
    require_ids,
    require_int,
)


def find_alternative_suppliers(product_id: str, excluded_supplier_ids: Optional[List[str]] = None,
                               dataset: Optional[SupplyChainDataset] = None) -> SupplierSearch:
    """Active suppliers of the product, excluding the disrupted one(s). Rejections carry reasons."""
    require_id("product_id", product_id, PRODUCT)
    excluded = require_ids("excluded_supplier_ids", excluded_supplier_ids, SUPPLIER)
    ds = dataset_or_default(dataset)
    candidates, rejected = [], []
    for s in ds.suppliers:
        if product_id not in s.supported_products:
            continue
        if s.supplier_id in excluded:
            rejected.append(RejectedOption(option_id=s.supplier_id, reason="disrupted/excluded supplier"))
        elif not s.is_active:
            rejected.append(RejectedOption(option_id=s.supplier_id, reason="supplier inactive"))
        else:
            candidates.append(s)
    candidates.sort(key=lambda s: (s.lead_time_days, -s.reliability_score, s.supplier_id))
    return SupplierSearch(product_id=product_id, excluded_supplier_ids=excluded,
                          candidates=candidates, rejected=rejected)


def check_supplier_capacity(supplier_ids: List[str], product_id: str, required_quantity: int,
                            deadline_days: int,
                            dataset: Optional[SupplyChainDataset] = None) -> CapacityReport:
    """Check each supplier's status, capacity and lead time against a quantity and deadline."""
    ids = require_ids("supplier_ids", supplier_ids, SUPPLIER)
    if len(set(ids)) != len(ids):
        raise ToolInputError("supplier_ids contains duplicates")
    require_id("product_id", product_id, PRODUCT)
    require_int("required_quantity", required_quantity, 1)
    require_int("deadline_days", deadline_days, 1)
    ds = dataset_or_default(dataset)
    checks = [_check_one(sid, product_id, required_quantity, deadline_days, ds) for sid in ids]
    return CapacityReport(product_id=product_id, required_quantity=required_quantity,
                          deadline_days=deadline_days, checks=checks)


def _check_one(supplier_id: str, product_id: str, required_quantity: int, deadline_days: int,
               ds: SupplyChainDataset) -> SupplierCapacityCheck:
    sup = next((s for s in ds.suppliers if s.supplier_id == supplier_id), None)
    if sup is None:
        raise ToolInputError(f"unknown supplier {supplier_id}")
    supports = product_id in sup.supported_products
    capacity_ok = sup.available_capacity >= required_quantity
    lead_ok = sup.lead_time_days <= deadline_days
    problems = []
    if not sup.is_active:
        problems.append("inactive")
    if not supports:
        problems.append(f"does not supply {product_id}")
    if not capacity_ok:
        problems.append(f"capacity {sup.available_capacity} < {required_quantity}")
    if not lead_ok:
        problems.append(f"lead time {sup.lead_time_days}d > deadline {deadline_days}d")
    reason = "; ".join(problems) if problems else (
        f"capacity {sup.available_capacity} >= {required_quantity}, lead time "
        f"{sup.lead_time_days}d <= {deadline_days}d")
    return SupplierCapacityCheck(
        supplier_id=supplier_id, product_id=product_id, is_active=sup.is_active, supports_product=supports,
        available_capacity=sup.available_capacity, lead_time_days=sup.lead_time_days,
        required_quantity=required_quantity, deadline_days=deadline_days, sufficient_capacity=capacity_ok,
        within_deadline=lead_ok, feasible=not problems, reason=reason,
    )
