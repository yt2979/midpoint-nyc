"""Discover a bounded, changing shortlist; measured routes decide fairness."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from itertools import combinations

from maps_client import _distance


def distance(a, b):
    return _distance(a["latitude"], a["longitude"], b)


def _safe_lookup(call, *args):
    try:
        return call(*args)
    except Exception:
        return {"error":"Places lookup failed. Retry with a precise address or check the server connection."}


def discover_candidates(members, client):
    """Up to 4 origin lookups + 3 nearby searches; never invent a fallback."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        resolved = list(pool.map(lambda m: _safe_lookup(client.resolve_origin, m["address"]), members))
    origins, errors = [], []
    for member, result in zip(members, resolved):
        if "error" in result:
            errors.append({"member":member["name"], "error":result["error"]})
        else:
            origins.append({"member":member["name"], **result})
    if errors:
        return {"error":"Could not resolve every origin. Check the starting address or station and borough for the people listed below.",
                "origin_errors":errors}

    # Limits bias search areas only. The fairness tool still enforces them using
    # Google Routes times; geographic distances never establish feasibility.
    weights = [1/(m.get("max_minutes") or 60) for m in members]
    center = {key:sum(o["location"][key]*w for o,w in zip(origins,weights))/sum(weights)
              for key in ("latitude", "longitude")}
    locations = [o["location"] for o in origins]
    farthest = max(combinations(locations, 2), key=lambda pair:distance(*pair))
    spread = max(distance(center, loc) for loc in locations)
    radius = max(1500.0, min(4500.0, spread*.45))
    centers = [center]
    for point in farthest:
        shifted = {key:(2*center[key]+point[key])/3 for key in center}
        if all(distance(shifted, existing) >= 600 for existing in centers):
            centers.append(shifted)
    driving_only = all(m.get("travel_mode") == "DRIVE" for m in members)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda c:_safe_lookup(client.meeting_places,c,radius,driving_only), centers))
    warnings, pool_places = [], []
    for index, result in enumerate(results):
        if "error" in result:
            warnings.append({"search_area":index+1, "error":result["error"]})
            continue
        for place in result["places"]:
            # A second entrance with the same name is still the same station.
            if any(place["place_id"] == old["place_id"] or
                   (place["name"].strip().casefold() == old["name"].strip().casefold() and
                    distance(place["location"], old["location"]) < 350) for old in pool_places):
                continue
            pool_places.append(place)
    if not pool_places:
        return {"error":"No usable meeting places found near this group. Try more precise origins or name meeting places to compare.",
                "search_errors":warnings}

    def score(place):
        ds = [distance(place["location"], loc) for loc in locations]
        return max(d*w for d,w in zip(ds,weights)), sum(ds), place["place_id"]
    ranked = sorted(pool_places, key=score)
    spacing = max(300.0, min(1200.0, spread*.15))
    selected = []
    for place in ranked:
        if all(distance(place["location"], old["location"]) >= spacing for old in selected):
            selected.append(place)
        if len(selected) == 3:
            break
    return {"candidates":selected, "origins":origins,
            "search_areas":[{"center":c,"radius_meters":radius} for c in centers],
            "places_found":len(pool_places), "warnings":warnings,
            "source":"Google Maps / Places API (New)", "observed_at":datetime.now(timezone.utc).isoformat(),
            "method":"Search real places near the group's locations; shortlist distinct places with a geographic heuristic. Personal limits bias the search. Only measured travel times decide fairness.",
            "scope":"Up to three dynamic candidates from a bounded geographic search, not every place in NYC."}
