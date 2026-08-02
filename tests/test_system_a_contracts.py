from __future__ import annotations

import ast
from pathlib import Path

from jsonschema import Draft202012Validator

from backtesting.system_a.contracts import REQUIRED_LAUNCH_FIELDS, base_inventory, inventory_schema


def test_inventory_is_formal_complete_and_distinguishes_route_share_proxy():
    inventory = base_inventory()
    Draft202012Validator(inventory_schema()).validate(inventory)
    fields = {row["canonical_field_name"]: row for row in inventory["fields"]}
    assert set(REQUIRED_LAUNCH_FIELDS) <= set(fields)
    assert fields["route_share"]["availability_status"] == "MISSING"
    assert fields["targets_per_team_dropback_proxy"]["availability_status"] == "DERIVABLE"
    assert fields["route_share"]["canonical_field_name"] != fields["targets_per_team_dropback_proxy"]["canonical_field_name"]


def test_system_a_workflow_has_no_sportsbook_dependency():
    forbidden_parts = {"sportsbook", "odds", "vig", "clv", "bankroll", "betting", "promotion", "ticket"}
    package = Path(__file__).parents[1] / "backtesting" / "system_a"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        for imported in imports:
            assert not (set(imported.lower().replace("-", "_").split(".")) & forbidden_parts), (path, imported)
