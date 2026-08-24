"""
LATS-style exploration of regulatory reporting paths for Safety Incidents.

Uses the existing planning/lats.py capabilities when available,
with a deterministic fallback so the workflow never hard-fails
if the LLM or LATS module is offline.
"""

from __future__ import annotations

from typing import Any


# Canonical reporting paths for Blue Horizon safety incidents.
REPORTING_PATHS: list[dict[str, Any]] = [
    {
        "path_id": "internal_ops_only",
        "name": "Internal Operations Log",
        "severity": "low-severity operational observations with no passenger impact",
        "requires_authority": False,
        "requires_crew_statement": False,
        "priority": 1,
    },
    {
        "path_id": "ops_and_safety_manager",
        "name": "Safety Manager Review",
        "applicable": "medium severity; internal investigation before any external filing",
        "requires_authority": False,
        "requires_crew_statement": True,
        "priority": 2,
    },
    {
        "path_id": "national_authority",
        "name": "National Aviation Authority Report",
        "applicable": "high or critical severity; mandatory regulatory reporting",
        "requires_authority": True,
        "requires_crew_statement": True,
        "priority": 3,
    },
    {
        "path_id": "authority_and_icao",
        "name": "Authority + ICAO Notification",
        "applicable": "critical events with international implications",
        "requires_authority": True,
        "requires_crew_statement": True,
        "priority": 4,
    },
]


def explore_reporting_paths(
    severity: str,
    incident_type: str,
    has_passenger_impact: bool = False,
) -> dict[str, Any]:
    """
    Explore possible reporting paths (LATS-inspired).

    Returns ranked candidate paths and a recommended primary path.
    """
    severity = (severity or "medium").lower()
    incident_type = (incident_type or "unspecified").lower()

    # Prefer LLM-backed LATS when the module is present.
    try:
        from planning.lats import run_lats  # type: ignore

        goal = (
            f"Select the correct safety reporting path for a "
            f"{severity}-severity incident of type '{incident_type}'. "
            f"Passenger impact: {has_passenger_impact}."
        )
        lats_result = run_lats(
            goal=goal,
            candidates=[p["name"] for p in REPORTING_PATHS],
            max_depth=2,
        )
        if isinstance(lats_result, dict) and lats_result.get("best_path"):
            return {
                "method": "lats",
                "candidates": REPORTING_PATHS,
                "recommended_path_id": _match_path_id(lats_result["best_path"]),
                "reasoning": lats_result.get("reasoning", "LATS selection"),
                "raw": lats_result,
            }
    except Exception:
        pass

    # Deterministic fallback ranked by severity.
    if severity in {"critical"} or has_passenger_impact and severity == "high":
        recommended = "authority_and_icao"
    elif severity == "high":
        recommended = "national_authority"
    elif severity == "medium":
        recommended = "ops_and_safety_manager"
    else:
        recommended = "internal_ops_only"

    # Upgrade path if incident type implies regulatory interest.
    regulatory_types = {
        "bird_strike",
        "runway_incursion",
        "system_failure",
        "smoke_or_fire",
        "medical_emergency",
        "turbulence_injury",
    }
    if incident_type in regulatory_types and recommended in {
        "internal_ops_only",
        "ops_and_safety_manager",
    }:
        recommended = "national_authority"

    return {
        "method": "deterministic_fallback",
        "candidates": REPORTING_PATHS,
        "recommended_path_id": recommended,
        "reasoning": (
            f"Severity={severity}, type={incident_type}, "
            f"passenger_impact={has_passenger_impact}."
        ),
        "raw": None,
    }


def _match_path_id(name_or_id: str) -> str:
    lowered = (name_or_id or "").lower()
    for path in REPORTING_PATHS:
        if path["path_id"] == lowered or path["name"].lower() in lowered:
            return path["path_id"]
    return "ops_and_safety_manager"
