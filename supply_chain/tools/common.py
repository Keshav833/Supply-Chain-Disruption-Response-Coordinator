"""Shared validation helpers and output models for the ten agent tools (PHASE_4).

Tools are plain, deterministic Python functions over the simulated datasets.
They validate their inputs (raising ToolInputError) and return validated
Pydantic models. They never return a hardcoded success value.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field, model_validator

from supply_chain.schemas import (
    PRODUCT_ID_PATTERN,
    ROUTE_ID_PATTERN,
    SITE_ID_PATTERN,
    SUPPLIER_ID_PATTERN,
    WAREHOUSE_ID_PATTERN,
    RouteRecord,
    StockLocation,
    SupplierCapacityCheck,
    SupplierRecord,
    SupplyChainDataset,
)


class ToolInputError(ValueError):
    """A tool received invalid input."""


class ToolExecutionError(RuntimeError):
    """A tool could not produce a valid result (e.g. referenced record missing)."""


def dataset_or_default(dataset: Optional[SupplyChainDataset]) -> SupplyChainDataset:
    if dataset is not None:
        return dataset
    from supply_chain.data.sample_data import DATASET
    return DATASET


def require_id(name: str, value: Any, pattern: str) -> str:
    if not isinstance(value, str) or not re.match(pattern, value):
        raise ToolInputError(f"{name} must match {pattern}, got {value!r}")
    return value


def require_int(name: str, value: Any, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolInputError(f"{name} must be an integer, got {value!r}")
    if value < minimum:
        raise ToolInputError(f"{name} must be >= {minimum}, got {value}")
    return value


def require_ids(name: str, values: Optional[Iterable[Any]], pattern: str) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raise ToolInputError(f"{name} must be a list of ids, not a string")
    return [require_id(name, v, pattern) for v in values]


PRODUCT, WAREHOUSE, SITE, SUPPLIER, ROUTE = (
    PRODUCT_ID_PATTERN, WAREHOUSE_ID_PATTERN, SITE_ID_PATTERN, SUPPLIER_ID_PATTERN, ROUTE_ID_PATTERN)


# --------------------------------------------------------------------------- outputs

class DisruptionValidation(BaseModel):
    incident_id: Optional[str]
    is_valid: bool
    errors: List[str]
    severity: str
    requires_immediate_action: bool
    validation_message: str

    @model_validator(mode="after")
    def _consistent(self) -> "DisruptionValidation":
        if self.is_valid == bool(self.errors):
            raise ValueError("is_valid must be True iff there are no errors")
        if not self.is_valid and (self.severity != "UNKNOWN" or self.requires_immediate_action):
            raise ValueError("invalid reports have UNKNOWN severity and no immediate action")
        return self


class AffectedSites(BaseModel):
    incident_id: str
    affected_sites: List[str] = Field(..., min_length=1)
    destination_id: str
    blocked_entity: Optional[str]
    related_suppliers: List[str]
    related_routes: List[str]
    impact_description: str


class InventoryLookup(BaseModel):
    product_id: str
    warehouse_id: Optional[str]  # None = all warehouses
    records: List[StockLocation]


class StockShortage(BaseModel):
    available_stock: int = Field(..., ge=0)
    safety_stock: int = Field(..., ge=0)
    required_quantity: int = Field(..., gt=0)
    usable_stock: int = Field(..., ge=0)
    shortage: int
    replenishment_required: int = Field(..., ge=0)
    safety_stock_met: bool
    can_fulfil_from_stock: bool

    @model_validator(mode="after")
    def _consistent(self) -> "StockShortage":
        if self.usable_stock != max(0, self.available_stock - self.safety_stock):
            raise ValueError("usable_stock inconsistent")
        if self.shortage != self.required_quantity + self.safety_stock - self.available_stock:
            raise ValueError("shortage inconsistent")
        if self.replenishment_required != max(0, self.shortage):
            raise ValueError("replenishment_required inconsistent")
        return self


class RejectedOption(BaseModel):
    option_id: str
    reason: str


class SupplierSearch(BaseModel):
    product_id: str
    excluded_supplier_ids: List[str]
    candidates: List[SupplierRecord]
    rejected: List[RejectedOption]

    @model_validator(mode="after")
    def _consistent(self) -> "SupplierSearch":
        for s in self.candidates:
            if not s.is_active or self.product_id not in s.supported_products \
                    or s.supplier_id in self.excluded_supplier_ids:
                raise ValueError(f"{s.supplier_id} is not a valid alternative candidate")
        return self


class RouteCandidate(BaseModel):
    route: RouteRecord
    origin_usable_stock: int = Field(..., ge=0)
    stock_sufficient: bool


class RouteSearch(BaseModel):
    destination_id: str
    product_id: str
    required_quantity: int = Field(..., gt=0)
    excluded_route_ids: List[str]
    candidates: List[RouteCandidate]
    rejected: List[RejectedOption]

    @model_validator(mode="after")
    def _consistent(self) -> "RouteSearch":
        for c in self.candidates:
            r = c.route
            if not r.is_available or r.destination != self.destination_id or r.route_id in self.excluded_route_ids:
                raise ValueError(f"{r.route_id} is not a valid candidate")
            if c.stock_sufficient != (c.origin_usable_stock >= self.required_quantity):
                raise ValueError(f"{r.route_id}: stock_sufficient inconsistent")
        return self


class RouteDelay(BaseModel):
    route_id: str
    is_available: bool
    normal_transit_days: int = Field(..., gt=0)
    current_delay_days: int = Field(..., ge=0)
    total_delivery_days: int = Field(..., gt=0)
    deadline_days: int = Field(..., gt=0)
    slack_days: int
    within_deadline: bool

    @model_validator(mode="after")
    def _consistent(self) -> "RouteDelay":
        if self.total_delivery_days != self.normal_transit_days + self.current_delay_days:
            raise ValueError("total_delivery_days inconsistent")
        if self.slack_days != self.deadline_days - self.total_delivery_days:
            raise ValueError("slack_days inconsistent")
        if self.within_deadline != (self.is_available and self.slack_days >= 0):
            raise ValueError("within_deadline requires an available route with slack >= 0")
        return self


class RouteDelayReport(BaseModel):
    deadline_days: int = Field(..., gt=0)
    delays: List[RouteDelay]


class CapacityReport(BaseModel):
    product_id: str
    required_quantity: int = Field(..., gt=0)
    deadline_days: int = Field(..., gt=0)
    checks: List[SupplierCapacityCheck]


def to_json(model: BaseModel) -> str:
    return model.model_dump_json()


def as_dict(model: BaseModel) -> Dict[str, Any]:
    return model.model_dump(mode="json")
