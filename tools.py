"""Model-visible contracts and session-scoped execution of four tools."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import uuid

from jsonschema import Draft202012Validator

from fairness import evaluate, departures, parse_time
from meeting_candidates import discover_candidates


def field(kind, description, **extra):
    return {"type":kind,"description":description,**extra}


def tool(name, description, properties, required):
    return {"type":"function","function":{"name":name,"description":description,
            "parameters":{"type":"object","properties":properties,"required":required,"additionalProperties":False}}}


MEMBER_SCHEMA = {"type":"object","properties":{
    "name":field("string","Unique member name used to identify constraints.",minLength=1,maxLength=60),
    "address":field("string","Precise NYC departure street address or named station with borough.",minLength=5,maxLength=250),
    "travel_mode":field("string","TRANSIT uses public transport; DRIVE estimates driving, not Uber pickup or prices.",enum=["TRANSIT","DRIVE"]),
    "max_minutes":field("number","Optional hard maximum one-way travel minutes. Omit to preserve this person's current limit. To remove one, pass null in evaluate_meeting_fairness instead.",exclusiveMinimum=0,maximum=180)},
    "required":["name","address","travel_mode"],"additionalProperties":False}
CANDIDATE_SCHEMA = {"type":"object","properties":{
    "name":field("string","Short destination label; distinguish candidates clearly.",minLength=1,maxLength=80),
    "address":field("string","Exact NYC destination street/station address, or exact venue address returned by Places.",minLength=5,maxLength=250)},
    "required":["name","address"],"additionalProperties":False}
ROUTE_ID = field("string","Server-generated route_set_id returned by get_group_routes in THIS session; never invent it.",minLength=1,maxLength=80)
CANDIDATE_ID = field("string","Candidate id such as c1 from that route set, not a place name.",minLength=1,maxLength=20)

TOOLS = [
    tool("get_group_routes", "Find and measure meeting options for 2–4 friends. OMIT candidates when the user asks you to find a spot: Places API resolves origins and finds up to 3 real places near this group, then Routes API measures every person's trip. No fixed default places. Supply candidates only for destinations the user specified or exact Places-returned venues to recheck. Up to 7 discovery + 12 route requests; do not repeat unchanged queries. Returns trusted route_set_id, candidate search evidence and complete per-person observations/errors. When origins, destinations, modes or meeting time change, fetch new routes. For leaving now, omit meeting_time.", {
        "members":field("array","2–4 friends with unique names, precise origins, individual modes and optional numeric hard travel limits. Omitted limits persist for each named member in this chat; use evaluate_meeting_fairness to remove a limit.",items=MEMBER_SCHEMA,minItems=2,maxItems=4),
        "candidates":field("array","Optional 1–3 exact destinations explicitly specified by the user or returned by Places. Omit entirely to discover dynamic candidates from this group's origins. Do not supply guessed default places. Optimum is among the measured candidates only.",items=CANDIDATE_SCHEMA,minItems=1,maxItems=3),
        "meeting_time":field("string","Optional FUTURE meeting date and time with explicit timezone offset, e.g. 2026-12-12T18:00:00-05:00 (New York winter). Transit queries arrival-by; driving approximates departure one hour earlier.",minLength=20,maxLength=40)},["members"]),
    tool("evaluate_meeting_fairness", "Original deterministic fairness/constraint tool. Read a trusted route set; rank feasible candidates by minimizing longest commute, then total commute, or compare total_time. Missing/failed routes cannot win. Return each person's time, spread, violations and explicit no-solution conflicts. Never relax limits silently. Call this in the CURRENT user request before searching places or planning departures, including after a new route query or changing/removing a limit. Reuse route data for constraints-only changes without new API calls.", {
        "route_set_id":ROUTE_ID,
        "objective":field("string","fair minimizes longest commute (default); total_time minimizes combined commute. Compare both to explain tradeoff.",enum=["fair","total_time"]),
        "max_minutes":field("object","Overrides of individual hard limits keyed by member name (case-insensitive). Unmentioned limits stay unchanged. Null explicitly removes one person's limit only with user consent.",additionalProperties={"type":["number","null"],"exclusiveMinimum":0,"maximum":180},maxProperties=4)},["route_set_id"]),
    tool("search_nearby_places", "First call evaluate_meeting_fairness in this user request, applying any requested limit changes. Only a candidate that passes the current limits may be used. Find up to three real restaurants, cafes or activities within a 1km straight-line radius of a measured candidate. Google Places (New) returns maps links and only observed metadata. Keyword relevance is not verified dietary safety or accessibility. Listings can be seasonal. A nearby venue is NOT automatically fair: recheck routes to its exact address and run fairness before calling it the final fair meetup.", {
        "route_set_id":ROUTE_ID,"candidate_id":CANDIDATE_ID,
        "category":field("string","restaurant for meals, cafe for coffee, activity for parks/museums/sights.",enum=["restaurant","cafe","activity"]),
        "keyword":field("string","Optional search preference such as vegetarian or museum. Missing metadata means unknown; do not claim guaranteed diet compatibility.",minLength=1,maxLength=120)},["route_set_id","candidate_id","category"]),
    tool("plan_group_departures", "After querying routes WITH meeting_time, call evaluate_meeting_fairness in this user request and select a feasible candidate. Then compute each person's leave-by estimate in America/New_York from that trusted route set. Uses actual transit schedule where supplied and includes a buffer. Do not use an old leaving-now route set for a future meetup: first query routes with that meeting_time. Driving excludes pickup/parking; estimates are not arrival guarantees.", {
        "route_set_id":ROUTE_ID,"candidate_id":CANDIDATE_ID,
        "buffer_minutes":field("integer","Extra time before the measured start; defaults to 10 minutes. Range 0–60.",minimum=0,maximum=60)},["route_set_id","candidate_id"]),
]
CONTRACTS = {t["function"]["name"]:t["function"]["parameters"] for t in TOOLS}


def _apply_limits(snapshot, limits):
    for member in snapshot["members"]:
        member["max_minutes"] = limits.get(member["name"].strip().casefold())


def _save_limits(state, limits):
    current = state.get("member_limits", {})
    if any(current.get(name) != value for name, value in limits.items()):
        # A decision about another snapshot is stale when the group's limits change.
        state.pop("fairness_checks", None)
    state.setdefault("member_limits", {}).update(limits)
    for snapshot in state.get("route_sets", {}).values():
        _apply_limits(snapshot, state["member_limits"])


def run_tool(name, args, state, client):
    """All numbers consumed by local tools originate in a session's API observations."""
    if name not in CONTRACTS:
        return {"error":f"Unknown tool {name}. Use one of {', '.join(CONTRACTS)}."}
    errors = list(Draft202012Validator(CONTRACTS[name]).iter_errors(args))
    if errors:
        error = errors[0]
        path = ".".join(map(str,error.absolute_path)) or "arguments"
        # Do not echo arbitrary supplied content (could include credentials).
        return {"error":f"Invalid {path}: check the documented {error.validator} rule for {name}.",
                "expected_rule":error.validator_value,"required":CONTRACTS[name]["required"]}
    try:
        if name == "get_group_routes":
            return _get_routes(args,state,client)
        snapshot = state.get("route_sets",{}).get(args["route_set_id"])
        if not snapshot:
            return {"error":"Unknown/expired route_set_id in this session. Call get_group_routes again; do not reuse another chat's id."}
        if name == "evaluate_meeting_fairness":
            current = state.get("member_limits", {})
            limits = {member["name"].strip().casefold():current.get(member["name"].strip().casefold(), member.get("max_minutes"))
                      for member in snapshot["members"]}
            names = {member["name"].strip().casefold():member["name"] for member in snapshot["members"]}
            seen = set()
            for supplied_name, value in args.get("max_minutes", {}).items():
                normalized = supplied_name.strip().casefold()
                if normalized not in names:
                    return {"error":f"Unknown member {supplied_name}. Use the names in get_group_routes."}
                if normalized in seen:
                    return {"error":"Specify each member's max_minutes only once."}
                seen.add(normalized)
                limits[normalized] = value
            effective = deepcopy(snapshot)
            _apply_limits(effective, limits)
            result = evaluate(effective,args.get("objective","fair"))
            if "error" not in result:
                _save_limits(state, limits)
                state.setdefault("fairness_checks", {})[args["route_set_id"]] = {
                    "turn_id": state.get("_turn_id", 0), "result": deepcopy(result)}
            return {"route_set_id":args["route_set_id"],**result}
        candidate = next((c for c in snapshot["candidates"] if c["id"] == args["candidate_id"]),None)
        if not candidate:
            return {"error":"Unknown candidate_id. Use c1/c2/c3 present in this route set."}
        check = state.get("fairness_checks", {}).get(args["route_set_id"])
        if not check or check["turn_id"] != state.get("_turn_id", 0):
            return {"error": "Call evaluate_meeting_fairness for this route_set_id in the current user request before searching places or planning departures. Apply all requested time-limit changes; use max_minutes with null to remove a limit only when the user asks.",
                    "route_set_id":args["route_set_id"]}
        checked_candidate = next((c for c in check["result"]["candidates"]
                                  if c["candidate_id"] == args["candidate_id"]), None)
        if not checked_candidate or not checked_candidate["feasible"]:
            return {"error": "This candidate does not pass the current time limits or has missing routes. Choose a feasible candidate, or apply a limit change explicitly requested by the user with evaluate_meeting_fairness. Do not say a limit was removed unless that tool applied null.",
                    "route_set_id":args["route_set_id"], "candidate_id":args["candidate_id"],
                    "violations":deepcopy(checked_candidate["violations"]) if checked_candidate else [],
                    "recommended_id":check["result"]["recommended_id"]}
        if name == "plan_group_departures":
            return {"route_set_id":args["route_set_id"],**departures(snapshot,args["candidate_id"],args.get("buffer_minutes",10))}
        locations = [r.get("destination_location") for r in snapshot["rows"] if r["candidate_id"] == args["candidate_id"] and not r.get("error")]
        location = next((loc for loc in locations if loc),None)
        if not location:
            return {"error":"No measured destination coordinates available. Rerun routes with an exact destination address."}
        result = client.places(location["latitude"],location["longitude"],args["category"],args.get("keyword"))
        return {"candidate":deepcopy(candidate),"center":location,**result}
    except Exception:
        # Keep actionable recovery, but never serialize third-party exception text/headers.
        return {"error":f"{name} could not complete. Recheck its arguments and retry; if persistent, inspect server configuration. No result can be recommended from this failure."}


def _get_routes(args,state,client):
    members=deepcopy(args["members"])
    if len({m["name"].strip().casefold() for m in members}) != len(members) or any(not m["name"].strip() for m in members):
        return {"error":"Give each person a nonempty unique name."}
    limits = state.get("member_limits", {}).copy()
    for member in members:
        normalized = member["name"].strip().casefold()
        if "max_minutes" in member:
            limits[normalized] = member["max_minutes"]
        member["max_minutes"] = limits.get(normalized)
    meeting=args.get("meeting_time")
    if meeting:
        try:
            parsed=parse_time(meeting)
            if parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc):
                return {"error":"The meeting time is in the past. Ask for a future NYC date/time, then query routes again."}
            meeting=parsed.isoformat()
        except (ValueError,TypeError,AttributeError):
            return {"error":"Provide a valid future meeting date/time with timezone offset; e.g. 2026-12-12T18:00:00-05:00. Ask if unclear."}
    if not client.configured:
        return {"error":"Server setup needed: GOOGLE_MAPS_API_KEY must be configured with Routes API and Places API (New) enabled. Never ask for a key in chat."}
    discovery = None
    origin_places = {}
    if "candidates" in args:
        chosen = deepcopy(args["candidates"])
        # Google sometimes gives a station only a borough/postcode address.
        # Preserve its observed identity for an exact same-session recheck.
        trusted = [c for s in reversed(list(state.get("route_sets", {}).values())) for c in s["candidates"]
                   if c.get("place_id")]
        for candidate in chosen:
            match = next((c for c in trusted if all(c[key].strip().casefold() == candidate[key].strip().casefold()
                                                   for key in ("name", "address"))), None)
            if match:
                candidate.update({key:value for key,value in deepcopy(match).items() if key != "id"})
    else:
        discovery = discover_candidates(members, client)
        if "error" in discovery:
            return discovery
        chosen = deepcopy(discovery["candidates"])
        origin_places = {o["member"]:o["place_id"] for o in discovery["origins"]}
    candidates = [{**c,"id":f"c{i+1}"} for i,c in enumerate(chosen)]
    def one(pair):
        member,candidate=pair
        try:
            origin = dict(member, place_id=origin_places[member["name"]]) if origin_places else member
            data=client.route(origin,candidate,meeting)
        except Exception:
            data={"error":"Route request failed. Retry this origin/destination with a precise address."}
        return {"member_id":member["name"],"candidate_id":candidate["id"],**data}
    pairs=[(m,c) for c in candidates for m in members]
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(one,pairs))
    rid="routes_"+uuid.uuid4().hex[:16]
    snapshot={"route_set_id":rid,"members":members,"candidates":candidates,"meeting_time":meeting,"rows":rows,
              "observed_at":datetime.now(timezone.utc).isoformat(),"source":"Google Maps / Routes API",
              "scope":"Finite candidate shortlist only. Durations are estimates; driving is not an Uber quote or pickup estimate."}
    if discovery is not None:
        snapshot["candidate_search"] = {key:value for key,value in discovery.items() if key != "candidates"}
    store=state.setdefault("route_sets",{})
    store[rid]=snapshot
    while len(store)>12:
        expired_id = next(iter(store))
        del store[expired_id]
        state.get("fairness_checks", {}).pop(expired_id, None)
    _save_limits(state, limits)
    return deepcopy(snapshot)
