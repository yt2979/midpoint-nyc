from datetime import datetime, timedelta, timezone
import requests
from maps_client import MapsClient
from tools import run_tool, TOOLS

def future_time():
    return (datetime.now(timezone.utc)+timedelta(days=2)).isoformat()

MEMBERS = [{"name":"Alice","address":"Columbia University, Broadway and W 116th St, NYC","travel_mode":"TRANSIT"},
           {"name":"Bob","address":"Atlantic Terminal, Brooklyn, NYC","travel_mode":"TRANSIT"}]
CANDIDATES = [{"name":"Union Square","address":"Union Square, Manhattan, NYC"},
              {"name":"Times Square","address":"Times Square, Manhattan, NYC"}]

class FakeMaps:
    configured = True
    def route(self, member, candidate, meeting_time=None):
        if member["name"] == "Bob" and candidate["name"] == "Times Square":
            return {"error":"No route found. Try a more precise address."}
        return {"duration_seconds":1800,"destination_location":{"latitude":40.7359,"longitude":-73.9911}}
    def places(self, *args, **kwargs):
        return {"places":[{"name":"Cafe","address":"123 Test St","location":{"latitude":40.73,"longitude":-73.99}}]}

def test_four_tools_have_described_arguments():
    assert len(TOOLS) == 4
    for tool in TOOLS:
        assert tool["function"]["description"]
        assert all(p.get("description") for p in tool["function"]["parameters"]["properties"].values())

def test_snapshot_scoped_to_session_and_failed_route_disqualifies():
    state = {}
    result = run_tool("get_group_routes", {"members":MEMBERS,"candidates":CANDIDATES}, state, FakeMaps())
    assert len(result["rows"]) == 4
    rid = result["route_set_id"]
    eval_args = {"route_set_id":rid}
    assert run_tool("evaluate_meeting_fairness", eval_args, state, FakeMaps())["recommended_id"] == "c1"
    assert "this session" in run_tool("evaluate_meeting_fairness",eval_args,{},FakeMaps())["error"]
    result["rows"][0]["duration_seconds"] = 0
    assert state["route_sets"][rid]["rows"][0]["duration_seconds"] == 1800

def test_bad_input_never_calls_api():
    for args in [{"members":[],"candidates":CANDIDATES},
                 {"members":MEMBERS,"candidates":CANDIDATES,"meeting_time":"yesterday"},
                 {"members":MEMBERS,"candidates":CANDIDATES,"meeting_time":"2020-01-01T18:00:00-05:00"}]:
        assert "error" in run_tool("get_group_routes",args,{},FakeMaps())
    assert "Unknown tool" in run_tool("shell",{}, {},FakeMaps())["error"]
    assert "error" in run_tool("evaluate_meeting_fairness",{"route_set_id":"invented","durations":[1,2]}, {},FakeMaps())

def test_selected_venue_and_departures_use_measured_destination():
    state = {}
    result = run_tool("get_group_routes",{"members":MEMBERS,"candidates":CANDIDATES,"meeting_time":future_time()},state,FakeMaps())
    args = {"route_set_id":result["route_set_id"],"candidate_id":"c1"}
    assert run_tool("evaluate_meeting_fairness", {"route_set_id":result["route_set_id"]},state,FakeMaps())["recommended_id"] == "c1"
    assert run_tool("search_nearby_places",dict(args,category="restaurant"),state,FakeMaps())["places"][0]["address"] == "123 Test St"
    assert len(run_tool("plan_group_departures",args,state,FakeMaps())["departures"]) == 2

def test_missing_api_key_actionable(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY",raising=False)
    result = MapsClient().route(MEMBERS[0],CANDIDATES[0])
    assert "GOOGLE_MAPS_API_KEY" in result["error"]

class Reply:
    def __init__(self,payload,status=200): self.payload,self.status_code=payload,status
    def json(self): return self.payload

def test_transit_request_uses_arrival_time_and_field_mask():
    captured = {}
    def post(url, **kwargs):
        captured.update(kwargs)
        return Reply({"routes":[{"duration":"1800.5s","distanceMeters":6000,
                                 "legs":[{"endLocation":{"latLng":{"latitude":40.73,"longitude":-73.99}},"steps":[]}]}]})
    result = MapsClient("secret",post=post).route(MEMBERS[0],CANDIDATES[0],future_time())
    assert result["duration_seconds"] == 1800.5
    assert "arrivalTime" in captured["json"]
    assert "departureTime" not in captured["json"]
    assert captured["timeout"] == (5,20)
    assert "routes.legs.endLocation" in captured["headers"]["X-Goog-FieldMask"]

def test_external_failures_do_not_expose_secret():
    def timeout(*args,**kwargs): raise requests.Timeout("secret")
    result = MapsClient("secret",post=timeout).route(MEMBERS[0],CANDIDATES[0])
    assert "secret" not in str(result)
    assert "timed out" in result["error"]
    denied = MapsClient("secret",post=lambda *a,**k: Reply({"error":{"message":"secret"}},403))
    assert "enable" in denied.route(MEMBERS[0],CANDIDATES[0])["error"].lower()

def test_places_keeps_missing_fields_unknown_and_uses_actual_center():
    captured={}
    def post(url,**kwargs):
        captured.update(kwargs)
        return Reply({"places":[{"id":"abc","displayName":{"text":"A cafe"},"formattedAddress":"123 Test St","location":{"latitude":40.73,"longitude":-73.99},"googleMapsUri":"https://maps.google.com/?cid=123"}]})
    result=MapsClient("secret",post=post).places(40.7359,-73.9911,"restaurant")
    assert result["places"][0]["rating"] is None
    assert result["places"][0]["open_now"] is None
    assert captured["json"]["locationRestriction"]["circle"]["center"]["latitude"] == 40.7359

def test_exact_venue_recheck_accepts_one_candidate():
    result = run_tool("get_group_routes",{"members":MEMBERS,"candidates":CANDIDATES[:1]}, {},FakeMaps())
    assert len(result["rows"]) == 2

def test_effective_group_limits_survive_requery_old_snapshot_and_removal():
    state = {}
    initial_members = [dict(member, max_minutes=30) for member in MEMBERS]
    first = run_tool("get_group_routes", {"members":initial_members,"candidates":CANDIDATES[:1]},state,FakeMaps())
    first_id = first["route_set_id"]
    assert run_tool("evaluate_meeting_fairness",{"route_set_id":first_id},state,FakeMaps())["recommended_id"] == "c1"
    tightened = run_tool("evaluate_meeting_fairness",{"route_set_id":first_id,"max_minutes":{"Alice":20}},state,FakeMaps())
    assert tightened["recommended_id"] is None
    venue = run_tool("get_group_routes",{"members":[dict(MEMBERS[0], name="ALICE"), MEMBERS[1]],
                                          "candidates":[{"name":"Cafe","address":"123 Test St, Manhattan, NYC"}]},state,FakeMaps())
    assert [member.get("max_minutes") for member in venue["members"]] == [20,30]
    assert run_tool("evaluate_meeting_fairness",{"route_set_id":venue["route_set_id"]},state,FakeMaps())["recommended_id"] is None
    assert run_tool("evaluate_meeting_fairness",{"route_set_id":first_id},state,FakeMaps())["recommended_id"] is None
    removed = run_tool("evaluate_meeting_fairness",{"route_set_id":first_id,"max_minutes":{"Alice":None}},state,FakeMaps())
    assert removed["recommended_id"] == "c1"
    again = run_tool("get_group_routes",{"members":MEMBERS,"candidates":CANDIDATES[:1]},state,FakeMaps())
    assert [member.get("max_minutes") for member in again["members"]] == [None,30]
    assert run_tool("evaluate_meeting_fairness",{"route_set_id":again["route_set_id"]},state,FakeMaps())["recommended_id"] == "c1"
    separate = run_tool("get_group_routes",{"members":MEMBERS,"candidates":CANDIDATES[:1]}, {},FakeMaps())
    assert all(member.get("max_minutes") is None for member in separate["members"])

def test_invalid_limit_override_does_not_partially_mutate_session():
    state = {}
    first = run_tool("get_group_routes",{"members":MEMBERS,"candidates":CANDIDATES[:1]},state,FakeMaps())
    bad = run_tool("evaluate_meeting_fairness",{"route_set_id":first["route_set_id"],
                                                  "max_minutes":{"Alice":20,"Nobody":10}},state,FakeMaps())
    assert "error" in bad
    next_set = run_tool("get_group_routes",{"members":MEMBERS,"candidates":CANDIDATES[:1]},state,FakeMaps())
    assert all(member.get("max_minutes") is None for member in next_set["members"])

def test_route_requery_null_is_invalid_and_cannot_clear_active_limit():
    state = {}
    first = run_tool("get_group_routes",{"members":[dict(MEMBERS[0],max_minutes=20),MEMBERS[1]],
                                         "candidates":CANDIDATES[:1]},state,FakeMaps())
    assert run_tool("evaluate_meeting_fairness",{"route_set_id":first["route_set_id"]},state,FakeMaps())["recommended_id"] is None
    invalid = run_tool("get_group_routes",{"members":[dict(MEMBERS[0],max_minutes=None),MEMBERS[1]],
                                           "candidates":CANDIDATES[:1]},state,FakeMaps())
    assert "error" in invalid
    next_set = run_tool("get_group_routes",{"members":MEMBERS,"candidates":CANDIDATES[:1]},state,FakeMaps())
    assert next_set["members"][0]["max_minutes"] == 20

def test_unchanged_members_keep_limits_when_group_grows_and_shrinks():
    state = {}
    first = run_tool("get_group_routes",{"members":[dict(MEMBERS[0],max_minutes=30),dict(MEMBERS[1],max_minutes=30)],
                                         "candidates":CANDIDATES[:1]},state,FakeMaps())
    assert run_tool("evaluate_meeting_fairness",{"route_set_id":first["route_set_id"],
                                                  "max_minutes":{"Alice":20}},state,FakeMaps())["recommended_id"] is None
    carol = {"name":"Carol","address":"Grand Central Terminal, Manhattan, NYC","travel_mode":"TRANSIT"}
    expanded = run_tool("get_group_routes",{"members":[MEMBERS[0],MEMBERS[1],carol],
                                            "candidates":CANDIDATES[:1]},state,FakeMaps())
    assert [member.get("max_minutes") for member in expanded["members"]] == [20,30,None]
    assert run_tool("evaluate_meeting_fairness",{"route_set_id":expanded["route_set_id"]},state,FakeMaps())["recommended_id"] is None
    assert run_tool("evaluate_meeting_fairness",{"route_set_id":expanded["route_set_id"],
                                                  "max_minutes":{"Alice":None}},state,FakeMaps())["recommended_id"] == "c1"
    assert run_tool("evaluate_meeting_fairness",{"route_set_id":first["route_set_id"]},state,FakeMaps())["recommended_id"] == "c1"
    reduced = run_tool("get_group_routes",{"members":MEMBERS,"candidates":CANDIDATES[:1]},state,FakeMaps())
    assert [member.get("max_minutes") for member in reduced["members"]] == [None,30]

class MeetupMaps:
    configured = True
    def __init__(self): self.place_calls = 0
    def route(self, member, candidate, meeting_time=None):
        times = {'Times Square': {'Alice': 18, 'Bob': 22},
                 'Union Square': {'Alice': 29, 'Bob': 18}}
        minutes = times[candidate['name']][member['name']]
        if meeting_time and candidate['name'] == 'Times Square':
            minutes = {'Alice': 23, 'Bob': 25}[member['name']]
        return {'duration_seconds': minutes*60,
                'destination_location': {'latitude':40.758, 'longitude':-73.9855}}
    def places(self, *args, **kwargs):
        self.place_calls += 1
        return {'places': [{'name': 'Test restaurant', 'address': '123 Test St'}]}


def test_downstream_tools_require_an_explicit_current_fairness_check():
    maps = MeetupMaps(); state = {'_turn_id':1}
    routes = run_tool('get_group_routes', {'members':MEMBERS,'candidates':CANDIDATES,
                                          'meeting_time':future_time()}, state, maps)
    args = {'route_set_id':routes['route_set_id'], 'candidate_id':'c2'}
    for name, extra in [('search_nearby_places', {'category':'restaurant'}),
                        ('plan_group_departures', {})]:
        result = run_tool(name, dict(args, **extra), state, maps)
        assert 'error' in result
        assert 'evaluate_meeting_fairness' in result['error']
    assert maps.place_calls == 0
    assert 'departures' not in result
    run_tool('evaluate_meeting_fairness', {'route_set_id':routes['route_set_id']}, state, maps)
    assert 'places' in run_tool('search_nearby_places',dict(args,category='restaurant'),state,maps)
    state['_turn_id'] += 1
    assert 'error' in run_tool('plan_group_departures', args, state, maps)


def test_user_sequence_cannot_search_an_infeasible_old_winner_or_forget_removal():
    maps = MeetupMaps(); state = {'_turn_id':1}
    first = run_tool('get_group_routes', {'members':MEMBERS,'candidates':CANDIDATES}, state, maps)
    rid = first['route_set_id']
    assert run_tool('evaluate_meeting_fairness', {'route_set_id':rid},state,maps)['recommended_id']=='c2'
    state['_turn_id'] += 1
    limited = run_tool('evaluate_meeting_fairness', {'route_set_id':rid,'max_minutes':{'Bob':20}},state,maps)
    assert limited['recommended_id']=='c1'
    state['_turn_id'] += 1
    args = {'route_set_id':rid,'candidate_id':'c2','category':'restaurant'}
    # A model that only says "removed" but jumps to Places gets no restaurant result.
    assert 'error' in run_tool('search_nearby_places',args,state,maps)
    run_tool('evaluate_meeting_fairness', {'route_set_id':rid},state,maps)
    blocked = run_tool('search_nearby_places',args,state,maps)
    assert 'error' in blocked and blocked['violations'][0]['member']=='Bob'
    assert maps.place_calls==0
    removed = run_tool('evaluate_meeting_fairness', {'route_set_id':rid,'max_minutes':{'Bob':None}},state,maps)
    assert removed['recommended_id']=='c2'
    assert state['member_limits']['bob'] is None
    assert 'places' in run_tool('search_nearby_places',args,state,maps)
    state['_turn_id'] += 1
    future = run_tool('get_group_routes', {'members':MEMBERS,'candidates':CANDIDATES,
                                         'meeting_time':future_time()},state,maps)
    plan_args = {'route_set_id':future['route_set_id'],'candidate_id':'c2'}
    assert future['members'][1]['max_minutes'] is None
    assert 'error' in run_tool('plan_group_departures',plan_args,state,maps)
    run_tool('evaluate_meeting_fairness', {'route_set_id':future['route_set_id']},state,maps)
    plan = run_tool('plan_group_departures',plan_args,state,maps)
    assert len(plan['departures'])==2
    assert plan['departures'][1]['travel_minutes']==25


def test_limit_changes_invalidate_other_route_set_checks():
    maps = MeetupMaps(); state = {}
    first = run_tool('get_group_routes', {'members':MEMBERS,'candidates':CANDIDATES},state,maps)
    second = run_tool('get_group_routes', {'members':MEMBERS,'candidates':CANDIDATES},state,maps)
    run_tool('evaluate_meeting_fairness', {'route_set_id':first['route_set_id']},state,maps)
    run_tool('evaluate_meeting_fairness', {'route_set_id':second['route_set_id'],'max_minutes':{'Bob':20}},state,maps)
    blocked = run_tool('search_nearby_places', {'route_set_id':first['route_set_id'],
                                              'candidate_id':'c2','category':'restaurant'},state,maps)
    assert 'error' in blocked and maps.place_calls==0


def test_invalid_request_preserves_google_reason_without_credentials():
    api_key = 'fake-test-key'
    rejected = MapsClient(api_key, post=lambda *a, **k: Reply({
        'error': {'status': 'INVALID_ARGUMENT', 'message': 'Invalid departureTime. key=' + api_key}
    }, 400))
    result = rejected.route(MEMBERS[0], CANDIDATES[0])
    assert result['http_status'] == 400
    assert 'Invalid departureTime' in result['error']
    assert api_key not in str(result)
    assert result['google_status'] == 'INVALID_ARGUMENT'
