from copy import deepcopy
import pytest
from fairness import evaluate, departures

@pytest.fixture
def routes():
    return {"members": [{"name": "Alice"}, {"name": "Bob"}],
            "candidates": [{"id": "A", "name": "A"}, {"id": "B", "name": "B"}],
            "meeting_time": "2026-12-12T00:10:00-05:00",
            "rows": [{"member_id": name, "candidate_id": dest, "duration_seconds": minutes*60}
                     for dest, times in [("A", [10,50]), ("B", [35,35])]
                     for name, minutes in zip(["Alice", "Bob"], times)]}

def test_minimax_differs_from_total_time(routes):
    assert evaluate(routes)["recommended_id"] == "B"
    assert evaluate(routes, "total_time")["recommended_id"] == "A"

def test_constraints_never_silently_relaxed(routes):
    result = evaluate(routes, max_minutes={"Alice":20,"Bob":40})
    assert result["recommended_id"] is None
    assert all(r["violations"] for r in result["candidates"])

def test_missing_failed_negative_routes_cannot_win(routes):
    routes["rows"] = routes["rows"][1:]
    assert evaluate(routes)["recommended_id"] == "B"
    routes["rows"][1]["duration_seconds"] = -1
    assert evaluate(routes)["recommended_id"] is None

def test_unknown_constraint_is_actionable(routes):
    assert "Unknown member" in evaluate(routes, max_minutes={"Carol":30})["error"]

def test_departures_cross_midnight(routes):
    result = departures(routes, "B", 10)
    assert result["departures"][0]["leave_by"] == "2026-12-11T23:25:00-05:00"

def test_departures_missing_schedule_and_bad_buffer(routes):
    routes["meeting_time"] = None
    assert "meeting time" in departures(routes,"A")["error"]
    assert "buffer" in departures(routes,"A",-2)["error"]

def test_departures_uses_absolute_time_across_dst(routes):
    routes["meeting_time"] = "2026-11-01T01:10:00-05:00"
    routes["rows"][2]["duration_seconds"] = 20*60
    assert departures(routes,"B",10)["departures"][0]["leave_by"] == "2026-11-01T01:40:00-04:00"

def test_incomplete_departure_plan_is_rejected(routes):
    routes["rows"].pop()
    assert "error" in departures(routes,"B")

def test_departures_honor_transit_that_arrives_before_meeting(routes):
    # Arrive-by search might find a train that arrives 15 minutes early.
    routes["rows"][2]["scheduled_departure_time"]="2026-12-11T23:20:00-05:00"
    routes["rows"][2]["scheduled_arrival_time"]="2026-12-11T23:55:00-05:00"
    result=departures(routes,"B",10)
    assert result["departures"][0]["leave_by"]=="2026-12-11T23:10:00-05:00"


def test_departures_reject_a_destination_that_breaks_a_hard_time_limit(routes):
    routes['members'][1]['max_minutes']=20
    result=departures(routes,'B')
    assert 'error' in result
    assert 'departures' not in result
    assert result['violations'][0]['member']=='Bob'
    routes['members'][1]['max_minutes']=None
    assert len(departures(routes,'B')['departures'])==2
