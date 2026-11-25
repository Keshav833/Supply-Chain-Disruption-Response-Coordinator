"""
Pydantic schemas for all data models used in the multi-agent system.

Schemas validate FORMAT and internal consistency of a single object.
Referential checks across datasets live in SupplyChainDataset (bottom of file);
business validation of an incoming report is the Monitor Agent's job.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Identifier formats
# ---------------------------------------------------------------------------
INCIDENT_ID_PATTERN = r"^INC-\d{4}$"
PRODUCT_ID_PATTERN = r"^[A-Z]+-\d{3}$"
WAREHOUSE_ID_PATTERN = r"^WH-[A-Z]{3}-\d{2}$"
CUSTOMER_ID_PATTERN = r"^CUST-[A-Z]{3}$"
SITE_ID_PATTERN = r"^(WH-[A-Z]{3}-\d{2}|CUST-[A-Z]{3})$"
SUPPLIER_ID_PATTERN = r"^SUP-\d{2}$"
ROUTE_ID_PATTERN = r"^RT-[A-Z]{3}-[A-Z]{3}-\d{2}$"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class DisruptionType(str, Enum):
    SUPPLIER_DELAY = "supplier_delay"
    WAREHOUSE_STOCKOUT = "warehouse_stockout"
    ROUTE_BLOCKAGE = "route_blockage"


class MitigationDecision(str, Enum):
    REORDER = "REORDER"
    REROUTE = "REROUTE"
    HOLD = "HOLD"
    UNKNOWN = "UNKNOWN"  # workflow could not produce a supported decision


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class ExecutionMode(str, Enum):
    """How a result was produced. Never label fallback output as an LLM call."""
    SEMANTIC_KERNEL_LLM = "semantic_kernel_llm"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class EvidenceStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"   # not posted; must NOT be read as a negative result
    FAILED = "FAILED"     # producing agent failed; no evidence exists


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------

class Product(BaseModel):
    product_id: str = Field(..., pattern=PRODUCT_ID_PATTERN)
    name: str = Field(..., min_length=1)
    unit: str = "units"


class Site(BaseModel):
    site_id: str = Field(..., pattern=SITE_ID_PATTERN)
    name: str = Field(..., min_length=1)
    city: str = Field(..., min_length=1)
    site_type: Literal["warehouse", "customer"]

    @model_validator(mode="after")
    def _type_matches_id(self) -> "Site":
        is_wh = self.site_id.startswith("WH-")
        if is_wh != (self.site_type == "warehouse"):
            raise ValueError(f"site_type '{self.site_type}' does not match id '{self.site_id}'")
        return self


# ---------------------------------------------------------------------------
# Incident (input)
# ---------------------------------------------------------------------------

class DisruptionIncident(BaseModel):
    incident_id: str = Field(..., pattern=INCIDENT_ID_PATTERN, description="e.g. INC-1001")
    disruption_type: DisruptionType
    affected_product_id: str = Field(..., pattern=PRODUCT_ID_PATTERN)
    affected_warehouse_id: str = Field(..., pattern=WAREHOUSE_ID_PATTERN)
    required_quantity: int = Field(..., gt=0)
    destination_id: Optional[str] = Field(
        default=None,
        pattern=SITE_ID_PATTERN,
        description="Where goods must arrive. None = replenish the affected warehouse.",
    )
    original_supplier_id: Optional[str] = Field(default=None, pattern=SUPPLIER_ID_PATTERN)
    original_route_id: Optional[str] = Field(default=None, pattern=ROUTE_ID_PATTERN)
    reported_delay_days: Optional[int] = Field(default=None, ge=0)
    deadline_days: int = Field(default=10, gt=0, description="Days within which demand must be met")
    reported_at: datetime = Field(default_factory=utc_now)
    description: str = ""

    @model_validator(mode="after")
    def _type_specific_fields(self) -> "DisruptionIncident":
        if self.disruption_type == DisruptionType.SUPPLIER_DELAY and not self.original_supplier_id:
            raise ValueError("supplier_delay incidents require original_supplier_id")
        if self.disruption_type == DisruptionType.ROUTE_BLOCKAGE:
            if not self.original_route_id:
                raise ValueError("route_blockage incidents require original_route_id")
            if not self.destination_id:
                raise ValueError("route_blockage incidents require destination_id")
        return self

    @property
    def effective_destination(self) -> str:
        return self.destination_id or self.affected_warehouse_id


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

class InventoryRecord(BaseModel):
    warehouse_id: str = Field(..., pattern=WAREHOUSE_ID_PATTERN)
    product_id: str = Field(..., pattern=PRODUCT_ID_PATTERN)
    available_stock: int = Field(..., ge=0)
    safety_stock: int = Field(..., ge=0)
    unit: str = "units"

    @property
    def usable_stock(self) -> int:
        """Stock that can be shipped without breaching safety stock."""
        return max(0, self.available_stock - self.safety_stock)


class StockLocation(BaseModel):
    """Another warehouse holding the product (possible REROUTE source)."""
    warehouse_id: str = Field(..., pattern=WAREHOUSE_ID_PATTERN)
    available_stock: int = Field(..., ge=0)
    safety_stock: int = Field(..., ge=0)
    usable_stock: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _usable_consistent(self) -> "StockLocation":
        if self.usable_stock != max(0, self.available_stock - self.safety_stock):
            raise ValueError("usable_stock must equal max(0, available_stock - safety_stock)")
        return self


class InventoryFindings(BaseModel):
    warehouse_id: str
    product_id: str
    available_stock: int = Field(..., ge=0)
    safety_stock: int = Field(..., ge=0)
    required_quantity: int = Field(..., gt=0)
    shortage: int  # required + safety - available; positive = shortage, negative = surplus
    safety_stock_met: bool
    recommendation: str
    # Added in PHASE_2 (optional so the pre-existing prototype keeps working)
    usable_stock: Optional[int] = Field(default=None, ge=0)
    replenishment_required: Optional[int] = Field(default=None, ge=0)
    alternative_stock_locations: List[StockLocation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

class SupplierRecord(BaseModel):
    supplier_id: str = Field(..., pattern=SUPPLIER_ID_PATTERN)
    name: str = Field(..., min_length=1)
    supported_products: List[str] = Field(..., min_length=1)
    available_capacity: int = Field(..., ge=0)
    lead_time_days: int = Field(..., gt=0)
    is_active: bool
    reliability_score: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("supported_products")
    @classmethod
    def _valid_products(cls, v: List[str]) -> List[str]:
        if len(set(v)) != len(v):
            raise ValueError("supported_products contains duplicates")
        return v


class SupplierCapacityCheck(BaseModel):
    """Result of check_supplier_capacity for one supplier (deterministic)."""
    supplier_id: str = Field(..., pattern=SUPPLIER_ID_PATTERN)
    product_id: str = Field(..., pattern=PRODUCT_ID_PATTERN)
    is_active: bool
    supports_product: bool
    available_capacity: int = Field(..., ge=0)
    lead_time_days: int = Field(..., gt=0)
    required_quantity: int = Field(..., gt=0)
    deadline_days: int = Field(..., gt=0)
    sufficient_capacity: bool
    within_deadline: bool
    feasible: bool
    reason: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "SupplierCapacityCheck":
        if self.sufficient_capacity != (self.available_capacity >= self.required_quantity):
            raise ValueError("sufficient_capacity inconsistent with capacity/quantity")
        if self.within_deadline != (self.lead_time_days <= self.deadline_days):
            raise ValueError("within_deadline inconsistent with lead time/deadline")
        expected = self.is_active and self.supports_product and self.sufficient_capacity and self.within_deadline
        if self.feasible != expected:
            raise ValueError("feasible must equal active AND supports AND capacity AND lead time")
        return self


class SupplierFindings(BaseModel):
    viable_suppliers: List[SupplierRecord]
    best_supplier: Optional[SupplierRecord]
    can_fulfill_by_deadline: bool
    recommendation: str
    # Added in PHASE_2
    excluded_supplier_ids: List[str] = Field(default_factory=list)
    deadline_days: Optional[int] = Field(default=None, gt=0)
    # Added in PHASE_5: per-candidate evidence so the Coordinator can re-check
    # capacity against inventory_findings.replenishment_required.
    product_id: Optional[str] = Field(default=None, pattern=PRODUCT_ID_PATTERN)
    required_quantity: Optional[int] = Field(default=None, gt=0)
    capacity_checks: List[SupplierCapacityCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "SupplierFindings":
        if self.best_supplier is not None:
            if self.best_supplier.supplier_id in self.excluded_supplier_ids:
                raise ValueError("best_supplier cannot be an excluded (disrupted) supplier")
            if self.best_supplier.supplier_id not in {s.supplier_id for s in self.viable_suppliers}:
                raise ValueError("best_supplier must be one of viable_suppliers")
        if self.can_fulfill_by_deadline != (self.best_supplier is not None):
            raise ValueError("can_fulfill_by_deadline must be True iff best_supplier is set")
        return self


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class RouteRecord(BaseModel):
    route_id: str = Field(..., pattern=ROUTE_ID_PATTERN)
    origin: str = Field(..., pattern=WAREHOUSE_ID_PATTERN)
    destination: str = Field(..., pattern=SITE_ID_PATTERN)
    is_available: bool
    normal_transit_days: int = Field(..., gt=0)
    current_delay_days: int = Field(..., ge=0)
    availability_score: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _consistent(self) -> "RouteRecord":
        if self.origin == self.destination:
            raise ValueError("route origin and destination must differ")
        if not self.is_available and self.availability_score != 0.0:
            raise ValueError("a blocked route must have availability_score 0.0")
        return self

    @property
    def total_delivery_days(self) -> int:
        return self.normal_transit_days + self.current_delay_days


class RouteAssessment(BaseModel):
    """Feasibility of one candidate route: origin stock AND delivery time."""
    route_id: str = Field(..., pattern=ROUTE_ID_PATTERN)
    origin: str = Field(..., pattern=WAREHOUSE_ID_PATTERN)
    destination: str = Field(..., pattern=SITE_ID_PATTERN)
    origin_usable_stock: int = Field(..., ge=0)  # 0 when the origin holds no record
    required_quantity: int = Field(..., gt=0)
    stock_sufficient: bool
    total_delivery_days: int = Field(..., gt=0)
    deadline_days: int = Field(..., gt=0)
    within_deadline: bool
    viable: bool
    reason: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "RouteAssessment":
        if self.stock_sufficient != (self.origin_usable_stock >= self.required_quantity):
            raise ValueError("stock_sufficient inconsistent with origin stock")
        if self.within_deadline != (self.total_delivery_days <= self.deadline_days):
            raise ValueError("within_deadline inconsistent with delivery days")
        if self.viable != (self.stock_sufficient and self.within_deadline):
            raise ValueError("a route is viable only if origin stock suffices AND it meets the deadline")
        return self


class RouteFindings(BaseModel):
    available_routes: List[RouteRecord]
    best_route: Optional[RouteRecord]
    minimum_delivery_days: Optional[int] = Field(default=None, ge=0)  # None = no candidate route
    can_deliver_by_deadline: bool
    recommendation: str
    # Added in PHASE_2
    destination_id: Optional[str] = Field(default=None, pattern=SITE_ID_PATTERN)
    blocked_route_ids: List[str] = Field(default_factory=list)
    # Added in PHASE_5
    product_id: Optional[str] = Field(default=None, pattern=PRODUCT_ID_PATTERN)
    required_quantity: Optional[int] = Field(default=None, gt=0)
    route_assessments: List[RouteAssessment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_blocked_best(self) -> "RouteFindings":
        if self.best_route is not None and not self.best_route.is_available:
            raise ValueError("best_route cannot be a blocked route")
        if self.best_route is not None and self.best_route.route_id in self.blocked_route_ids:
            raise ValueError("best_route cannot be a reported blocked route")
        if self.best_route is not None and self.route_assessments:
            match = [a for a in self.route_assessments if a.route_id == self.best_route.route_id]
            if not match or not match[0].viable:
                raise ValueError("best_route must have a viable assessment (origin stock + deadline)")
        if self.route_assessments and self.can_deliver_by_deadline != (self.best_route is not None):
            raise ValueError("can_deliver_by_deadline must be True iff best_route is set")
        return self


# ---------------------------------------------------------------------------
# Monitor assessment
# ---------------------------------------------------------------------------



class CostEstimate(BaseModel):
    """Cost estimate for one mitigation option."""
    option_type: Literal["REORDER", "REROUTE"]
    option_id: str = Field(..., min_length=1)
    cost_per_unit: float = Field(..., ge=0)
    total_cost: float = Field(..., ge=0)
    cost_category: str = Field(..., min_length=1)
    breakdown: Dict[str, float] = Field(default_factory=dict)


class CostFindings(BaseModel):
    """Cost Agent findings: cost estimates and recommendations."""
    incident_id: str = Field(..., pattern=INCIDENT_ID_PATTERN)
    cost_estimates: List[CostEstimate] = Field(default_factory=list)
    cheapest_option: Optional[str] = None
    cost_comparison: Dict[str, float] = Field(default_factory=dict)
    recommendation: str = Field(default="")


class IncidentAssessment(BaseModel):
    incident_id: str
    is_valid: bool
    validation_message: str
    affected_sites: List[str]
    severity: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"]
    requires_immediate_action: bool


# ---------------------------------------------------------------------------
# Decision and notifications
# ---------------------------------------------------------------------------

class Notification(BaseModel):
    """A party that SHOULD be notified. Delivery is simulated, never claimed."""
    party: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    channel: Literal["simulated"] = "simulated"
    delivered: Literal[False] = False


class MitigationResult(BaseModel):
    incident_id: str
    decision: MitigationDecision
    justification: str = Field(..., min_length=1)
    parties_to_notify: List[str]
    evidence_summary: Dict[str, Any]
    selected_option_id: Optional[str] = None  # supplier_id (REORDER) or route_id (REROUTE)
    decided_at: datetime = Field(default_factory=utc_now)
    authorized: bool = False  # recommendation only; execution needs explicit human authorization
    execution_mode: ExecutionMode = ExecutionMode.DETERMINISTIC_FALLBACK
    proposed_by: Literal["llm", "deterministic"] = "deterministic"
    guardrail_check: Optional[Dict[str, Any]] = None

    @field_validator("execution_mode", mode="before")
    @classmethod
    def _legacy_mode(cls, v: Any) -> Any:
        # The pre-existing prototype used "deterministic"; map it explicitly.
        # A bare "llm" label is rejected: LLM mode must be claimed explicitly.
        return ExecutionMode.DETERMINISTIC_FALLBACK if v == "deterministic" else v

    @model_validator(mode="after")
    def _authorization(self) -> "MitigationResult":
        if self.authorized:
            raise ValueError("decisions are recommendations; authorized must stay False")
        return self


# ---------------------------------------------------------------------------
# Execution log entry
# ---------------------------------------------------------------------------

class AgentLogEntry(BaseModel):
    agent_name: str
    timestamp: datetime = Field(default_factory=utc_now)
    action: str
    section_updated: str
    evidence: Dict[str, Any]
    status: AgentStatus
    error: Optional[str] = None
    incident_id: Optional[str] = None
    execution_id: Optional[str] = None
    sequence: Optional[int] = Field(default=None, ge=0)
    execution_mode: Optional[ExecutionMode] = None


# ---------------------------------------------------------------------------
# Complete dataset with cross-reference validation
# ---------------------------------------------------------------------------

def _duplicates(keys: List[Any]) -> Set[Any]:
    seen: Set[Any] = set()
    return {k for k in keys if k in seen or seen.add(k)}


class SupplyChainDataset(BaseModel):
    products: List[Product]
    sites: List[Site]
    inventory: List[InventoryRecord]
    suppliers: List[SupplierRecord]
    routes: List[RouteRecord]
    incidents: List[DisruptionIncident]

    @model_validator(mode="after")
    def _referential_integrity(self) -> "SupplyChainDataset":
        errors: List[str] = []
        product_ids = {p.product_id for p in self.products}
        site_ids = {s.site_id for s in self.sites}
        warehouse_ids = {s.site_id for s in self.sites if s.site_type == "warehouse"}
        suppliers = {s.supplier_id: s for s in self.suppliers}
        routes = {r.route_id: r for r in self.routes}

        checks: List[Tuple[str, List[Any]]] = [
            ("product_id", [p.product_id for p in self.products]),
            ("site_id", [s.site_id for s in self.sites]),
            ("inventory (warehouse, product)", [(i.warehouse_id, i.product_id) for i in self.inventory]),
            ("supplier_id", [s.supplier_id for s in self.suppliers]),
            ("route_id", [r.route_id for r in self.routes]),
            ("incident_id", [i.incident_id for i in self.incidents]),
        ]
        for label, keys in checks:
            if dups := _duplicates(keys):
                errors.append(f"duplicate {label}: {sorted(dups)}")

        for rec in self.inventory:
            if rec.warehouse_id not in warehouse_ids:
                errors.append(f"inventory references unknown warehouse {rec.warehouse_id}")
            if rec.product_id not in product_ids:
                errors.append(f"inventory references unknown product {rec.product_id}")
        for sup in self.suppliers:
            for pid in sup.supported_products:
                if pid not in product_ids:
                    errors.append(f"{sup.supplier_id} supports unknown product {pid}")
        for rt in self.routes:
            if rt.origin not in warehouse_ids:
                errors.append(f"{rt.route_id} origin {rt.origin} is not a known warehouse")
            if rt.destination not in site_ids:
                errors.append(f"{rt.route_id} destination {rt.destination} is not a known site")
        for inc in self.incidents:
            tag = inc.incident_id
            if inc.affected_product_id not in product_ids:
                errors.append(f"{tag}: unknown product {inc.affected_product_id}")
            if inc.affected_warehouse_id not in warehouse_ids:
                errors.append(f"{tag}: unknown warehouse {inc.affected_warehouse_id}")
            if inc.destination_id and inc.destination_id not in site_ids:
                errors.append(f"{tag}: unknown destination {inc.destination_id}")
            if inc.original_supplier_id:
                sup = suppliers.get(inc.original_supplier_id)
                if sup is None:
                    errors.append(f"{tag}: unknown supplier {inc.original_supplier_id}")
                elif inc.affected_product_id not in sup.supported_products:
                    errors.append(f"{tag}: {sup.supplier_id} does not supply {inc.affected_product_id}")
            if inc.original_route_id:
                rt = routes.get(inc.original_route_id)
                if rt is None:
                    errors.append(f"{tag}: unknown route {inc.original_route_id}")
                else:
                    if rt.origin != inc.affected_warehouse_id:
                        errors.append(f"{tag}: route {rt.route_id} does not start at {inc.affected_warehouse_id}")
                    if inc.destination_id and rt.destination != inc.destination_id:
                        errors.append(f"{tag}: route {rt.route_id} does not end at {inc.destination_id}")
                    if inc.disruption_type == DisruptionType.ROUTE_BLOCKAGE and rt.is_available:
                        errors.append(f"{tag}: reported blocked route {rt.route_id} is marked available")
        if errors:
            raise ValueError("; ".join(errors))
        return self
