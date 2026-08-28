"""Display names for MCP service ids."""

_SERVICE_DISPLAY: dict[str, str] = {
    "budgettracker": "BudgetTracker",
    "homebase": "Homebase",
}


def display_service_name(service_id: str) -> str:
    return _SERVICE_DISPLAY.get(service_id, service_id.replace("_", " ").title())
