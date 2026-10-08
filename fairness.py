"""Deterministic decisions over observed route data; no model arithmetic."""
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NYC = ZoneInfo("America/New_York")


def parse_time(value):
    """Require an explicit timezone; normalize display to NYC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Include a date and timezone offset, e.g. 2026-12-12T18:00:00-05:00.")
    return parsed.astimezone(NYC)


def _seconds(row):
    value = row.get("duration_seconds")
    return value if (not row.get("error") and type(value) in (int, float)
                     and math.isfinite(value) and value >= 0) else None


def evaluate(routes, objective="fair", max_minutes=None):
    if objective not in ("fair", "total_time"):
        return {"error": "objective must be fair or total_time."}
    members = routes["members"]
    limits = {m["name"]: m.get("max_minutes") for m in members}
    for name, value in (max_minutes or {}).items():
        if name not in limits:
            return {"error": f"Unknown member {name}. Use the names in get_group_routes."}
        limits[name] = value
    for name, limit in limits.items():
        if limit is not None and (type(limit) not in (int, float) or not math.isfinite(limit) or limit <= 0):
            return {"error": f"max_minutes for {name} must be positive, or null to remove that limit."}
    records = []
    for candidate in routes["candidates"]:
        violations, journeys, times = [], [], []
        for member in members:
            name = member["name"]
            matching = [r for r in routes["rows"] if r["member_id"] == name and r["candidate_id"] == candidate["id"]]
            row = matching[0] if len(matching) == 1 else {}
            seconds = _seconds(row)
            if seconds is None:
                violations.append({"member": name, "reason": "route unavailable", "detail": row.get("error", "Missing or invalid route.")})
                journeys.append({"member": name, "minutes": None})
                continue
            times.append(seconds)
            journeys.append({"member": name, "minutes": round(seconds / 60, 1), "max_minutes": limits[name]})
            if limits[name] is not None and seconds > limits[name] * 60:
                violations.append({"member": name, "reason": "time limit exceeded",
                                   "minutes": round(seconds/60, 1), "max_minutes": limits[name],
                                   "over_by_minutes": round((seconds-limits[name]*60)/60, 1)})
        complete = len(times) == len(members)
        record = {"candidate_id": candidate["id"], "name": candidate["name"], "feasible": complete and not violations,
                  "journeys": journeys, "violations": violations,
                  "longest_minutes": round(max(times)/60, 1) if complete else None,
                  "total_minutes": round(sum(times)/60, 1) if complete else None,
                  "spread_minutes": round((max(times)-min(times))/60, 1) if complete else None}
        # Rank on full precision, never on rounded display values.
        record["_score"] = ((max(times),sum(times)) if objective == "fair" else (sum(times),max(times))) if complete else (math.inf,math.inf)
        records.append(record)
    feasible = sorted((r for r in records if r["feasible"]), key=lambda r: (*r["_score"],r["candidate_id"]))
    for r in records:
        del r["_score"]
    return {"objective": objective, "definition": "Minimize the longest commute; ties use total time." if objective == "fair" else "Minimize total commute; ties use longest time.",
            "recommended_id": feasible[0]["candidate_id"] if feasible else None,
            "ranked_feasible_ids": [r["candidate_id"] for r in feasible], "candidates": records,
            "scope": "Only the supplied candidate destinations, not all NYC locations.",
            "next_step": "Choose a feasible destination; recheck routes to a specific venue." if feasible else "No candidate satisfies all limits with complete routes. Explain blockers; ask before changing limits or try new candidates."}


def departures(routes, candidate_id, buffer_minutes=10):
    if type(buffer_minutes) is not int or not 0 <= buffer_minutes <= 60:
        return {"error": "buffer_minutes must be an integer from 0 to 60."}
    if not routes.get("meeting_time"):
        return {"error": "Specify a future meeting time and rerun get_group_routes before planning departures."}
    try:
        meeting = parse_time(routes["meeting_time"])
    except (ValueError, TypeError, AttributeError):
        return {"error": "The route set needs a meeting time with date and timezone offset."}
    if candidate_id not in {c["id"] for c in routes["candidates"]}:
        return {"error": "Unknown candidate_id. Use an id in the route set."}
    checked = evaluate(routes)
    if "error" in checked:
        return checked
    selected = next(c for c in checked["candidates"] if c["candidate_id"] == candidate_id)
    if not selected["feasible"]:
        return {"error": "This destination does not pass the current time limits or has missing routes. Check fairness and choose a feasible destination before planning departures.",
                "violations":selected["violations"]}
    plans = []
    for member in routes["members"]:
        matching = [r for r in routes["rows"] if r["member_id"] == member["name"] and r["candidate_id"] == candidate_id]
        row = matching[0] if len(matching) == 1 else {}
        seconds = _seconds(row)
        if seconds is None:
            return {"error": f"Route unavailable for {member['name']}; rerun routes before planning departures."}
        # UTC arithmetic avoids repeated/missing local hours during DST transitions.
        leave = (meeting.astimezone(timezone.utc) - timedelta(seconds=seconds, minutes=buffer_minutes)).astimezone(NYC)
        if member.get("travel_mode", "TRANSIT") == "TRANSIT" and row.get("scheduled_departure_time"):
            try:
                scheduled=parse_time(row["scheduled_departure_time"]).astimezone(timezone.utc)
                # The arrive-by train can arrive early; a later departure misses it.
                leave=min(leave.astimezone(timezone.utc),scheduled-timedelta(minutes=buffer_minutes)).astimezone(NYC)
            except (ValueError,TypeError,AttributeError):
                return {"error":f"Invalid transit schedule for {member['name']}. Rerun routes."}
        plans.append({"member": member["name"], "leave_by": leave.isoformat(),
                      "travel_minutes": round(seconds/60,1), "buffer_minutes": buffer_minutes,
                      "travel_mode": member.get("travel_mode", "TRANSIT"),
                      "route_departure_time": row.get("scheduled_departure_time"),
                      "route_arrival_time": row.get("scheduled_arrival_time"),
                      "assumption": row.get("time_assumption")})
    return {"candidate_id": candidate_id, "meeting_time": meeting.isoformat(), "timezone": "America/New_York",
            "departures": plans, "note": "Leave-by estimates include the chosen buffer. Check the supplied transit schedule; earlier trains may be needed. Driving estimates exclude parking and rideshare pickup waits. Recheck routes near departure."}
