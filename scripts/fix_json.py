import json

suppliers_data = [
  {"supplier_id": "SUP-01", "name": "TechSource India", "supported_products": ["LAPTOP-001", "TABLET-003"], "available_capacity": 0, "lead_time_days": 12, "is_active": False, "reliability_score": 0.60, "unit_cost": 500.0},
  {"supplier_id": "SUP-02", "name": "GlobalTech Supplies", "supported_products": ["LAPTOP-001", "PHONE-002"], "available_capacity": 150, "lead_time_days": 3, "is_active": True, "reliability_score": 0.95, "unit_cost": 450.0},
  {"supplier_id": "SUP-03", "name": "QuickParts Ltd", "supported_products": ["PHONE-002", "TABLET-003"], "available_capacity": 200, "lead_time_days": 2, "is_active": True, "reliability_score": 0.90, "unit_cost": 480.0},
  {"supplier_id": "SUP-04", "name": "SlowCo Manufacturing", "supported_products": ["LAPTOP-001"], "available_capacity": 50, "lead_time_days": 15, "is_active": True, "reliability_score": 0.70, "unit_cost": 420.0},
  {"supplier_id": "SUP-05", "name": "PrecisionElec", "supported_products": ["TABLET-003"], "available_capacity": 0, "lead_time_days": 5, "is_active": True, "reliability_score": 0.85, "unit_cost": 500.0}
]

with open("supply_chain/data/suppliers.json", "w", encoding="utf-8") as f:
    json.dump(suppliers_data, f, indent=2)
print("Fixed suppliers.json")

routes_data = [
  {"route_id": "RT-PUN-MUM-01", "origin": "WH-PUN-01", "destination": "CUST-MUM", "is_available": False, "normal_transit_days": 1, "current_delay_days": 5, "availability_score": 0.0, "cost_per_unit": 20.0, "expedited_fee_percent": 0.15},
  {"route_id": "RT-MUM-MUM-02", "origin": "WH-MUM-02", "destination": "CUST-MUM", "is_available": True, "normal_transit_days": 1, "current_delay_days": 0, "availability_score": 0.98, "cost_per_unit": 15.0, "expedited_fee_percent": 0.10},
  {"route_id": "RT-DEL-MUM-03", "origin": "WH-DEL-03", "destination": "CUST-MUM", "is_available": True, "normal_transit_days": 3, "current_delay_days": 1, "availability_score": 0.80, "cost_per_unit": 25.0, "expedited_fee_percent": 0.12},
  {"route_id": "RT-BLR-DEL-04", "origin": "WH-BLR-04", "destination": "CUST-DEL", "is_available": False, "normal_transit_days": 2, "current_delay_days": 7, "availability_score": 0.0, "cost_per_unit": 18.0, "expedited_fee_percent": 0.15},
  {"route_id": "RT-DEL-DEL-05", "origin": "WH-DEL-03", "destination": "CUST-DEL", "is_available": True, "normal_transit_days": 1, "current_delay_days": 0, "availability_score": 0.99, "cost_per_unit": 12.0, "expedited_fee_percent": 0.08},
  {"route_id": "RT-MUM-PUN-06", "origin": "WH-MUM-02", "destination": "WH-PUN-01", "is_available": True, "normal_transit_days": 1, "current_delay_days": 0, "availability_score": 0.95, "cost_per_unit": 10.0, "expedited_fee_percent": 0.10}
]

with open("supply_chain/data/routes.json", "w", encoding="utf-8") as f:
    json.dump(routes_data, f, indent=2)
print("Fixed routes.json")

print("All JSON files fixed successfully!")
