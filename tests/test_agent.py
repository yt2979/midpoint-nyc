from types import SimpleNamespace as NS
from agent import run_agent
from tests.test_tools import FakeMaps, MEMBERS, CANDIDATES
import json

class Message:
    def __init__(self,content=None,tool_calls=None):
        self.content,self.tool_calls=content,tool_calls
    def model_dump(self):
        return {"role":"assistant","content":self.content,
                **({"tool_calls":[{"id":c.id,"type":"function","function":{"name":c.function.name,"arguments":c.function.arguments}} for c in self.tool_calls]} if self.tool_calls else {})}

def call(name,args="{}",id="call_1"):
    return NS(id=id,function=NS(name=name,arguments=args))
def answer(content=None,calls=None):
    return NS(choices=[NS(message=Message(content,calls))])

def test_no_tool_explanation():
    messages=[{"role":"user","content":"Explain minimax"}]
    text,trace=run_agent(messages,{},completion=lambda **k: answer("Minimize the longest commute."),client=FakeMaps())
    assert "longest" in text
    assert trace == []

def test_invalid_tool_json_logged_and_model_can_recover():
    turns=iter([answer(calls=[call("get_group_routes","{bad")]),answer("Please provide two origins.")])
    messages=[{"role":"user","content":"Find a meetup"}]
    text,trace=run_agent(messages,{},completion=lambda **k: next(turns),client=FakeMaps())
    assert trace[0]["name"] == "get_group_routes"
    assert "error" in trace[0]["result"]
    assert messages[-2]["role"] == "tool"
    assert "origins" in text

def test_model_failure_preserves_completed_tool_trace():
    def completion(**kwargs):
        if len(kwargs["messages"]) == 1:
            return answer(calls=[call("get_group_routes",json.dumps({"members":MEMBERS,"candidates":CANDIDATES}))])
        raise RuntimeError("secret-key-do-not-expose")
    text,trace=run_agent([{"role":"user","content":"Meet"}],{},completion=completion,client=FakeMaps())
    assert len(trace) == 1
    assert "secret-key" not in text
    assert "Gemini" in text

def test_tool_loop_limit_retains_results():
    text,trace=run_agent([{"role":"user","content":"Meet"}],{},completion=lambda **k:answer(calls=[call("unknown")]),client=FakeMaps())
    assert len(trace) == 8
    assert "limit" in text.lower()

def test_nonfinite_json_arguments_rejected_before_execution():
    turns=iter([answer(calls=[call("get_group_routes",'{"members":[],"candidates":[],"meeting_time":NaN}')]),answer("Please use a valid meeting date.")])
    text,trace=run_agent([{"role":"user","content":"Meet"}],{},completion=lambda **k:next(turns),client=FakeMaps())
    assert "Invalid tool arguments JSON" in trace[0]["result"]["error"]


def test_agent_marks_a_new_turn_and_recovers_from_skipping_limit_removal():
    from tools import run_tool
    from tests.test_tools import MeetupMaps
    maps=MeetupMaps(); state={}
    first=run_tool('get_group_routes', {'members':MEMBERS,'candidates':CANDIDATES},state,maps)
    rid=first['route_set_id']
    run_tool('evaluate_meeting_fairness', {'route_set_id':rid,'max_minutes':{'Bob':20}},state,maps)
    turns=iter([
        answer(calls=[call('search_nearby_places',json.dumps({'route_set_id':rid,'candidate_id':'c2','category':'restaurant'}),'bad_search')]),
        answer(calls=[call('evaluate_meeting_fairness',json.dumps({'route_set_id':rid,'max_minutes':{'Bob':None}}),'remove_limit')]),
        answer(calls=[call('search_nearby_places',json.dumps({'route_set_id':rid,'candidate_id':'c2','category':'restaurant'}),'good_search')]),
        answer('Bob has no time limit now. Here is a nearby restaurant.')])
    text,trace=run_agent([{'role':'user','content':"Remove Bob's limit. Find restaurants near the best spot."}],
                         state,completion=lambda **kwargs:next(turns),client=maps)
    assert state['_turn_id']==1
    assert 'evaluate_meeting_fairness' in trace[0]['result']['error']
    assert state['member_limits']['bob'] is None
    assert maps.place_calls==1
    assert 'places' in trace[2]['result']
    assert 'no time limit' in text


def test_empty_reply_after_a_tool_retries_without_repeating_routes_or_saving_empty_history():
    turns=0
    state={}
    def completion(**kwargs):
        nonlocal turns
        turns+=1
        if turns==1:
            return answer(calls=[call('get_group_routes',json.dumps({'members':MEMBERS,'candidates':CANDIDATES}))])
        if turns==2:
            return answer()
        if turns==3:
            rid=next(iter(state['route_sets']))
            return answer(calls=[call('evaluate_meeting_fairness',json.dumps({'route_set_id':rid}),'check')])
        return answer('Union Square works for both people.')
    messages=[{'role':'user','content':'Find a fair meeting spot.'}]
    text,trace=run_agent(messages,state,completion=completion,client=FakeMaps())
    assert text=='Union Square works for both people.'
    assert [item['name'] for item in trace]==['get_group_routes','evaluate_meeting_fairness']
    assert len(state['route_sets'])==1
    assert not any(m['role']=='assistant' and not m.get('content') and not m.get('tool_calls') for m in messages)


def test_repeated_empty_replies_return_a_bounded_actionable_error_and_keep_the_trace():
    turns=0
    def completion(**kwargs):
        nonlocal turns
        turns+=1
        if turns==1:
            return answer(calls=[call('get_group_routes',json.dumps({'members':MEMBERS,'candidates':CANDIDATES}))])
        return answer()
    text,trace=run_agent([{'role':'user','content':'Find a fair spot.'}],{},completion=completion,client=FakeMaps())
    assert 'try again' in text.lower()
    assert turns==3, 'Two blank responses should stop rather than consuming every loop round.'
    assert len(trace)==1


def test_repeated_failed_routes_stop_without_repeating_external_calls():
    class DeniedMaps:
        configured = True
        calls = 0
        def route(self, *args):
            self.calls += 1
            return {'error': 'Routes API HTTP 403. Enable Routes API and billing; check API restrictions.', 'http_status': 403}
    maps = DeniedMaps()
    def completion(**kwargs):
        return answer(calls=[call('get_group_routes', json.dumps({'members': MEMBERS, 'candidates': CANDIDATES}))])
    messages = [{'role': 'user', 'content': 'Compare these spots.'}]
    text, trace = run_agent(messages, {}, completion=completion, client=maps)
    assert maps.calls == len(MEMBERS) * len(CANDIDATES)
    assert len(trace) == 2
    assert trace[-1]['result']['reused'] is True
    assert '403' in text and 'restrictions' in text.lower()
    assert messages[-1]['role'] == 'assistant'


def test_repeated_successful_routes_reuse_observations_then_allow_fairness():
    class CountMaps(FakeMaps):
        calls = 0
        def route(self, *args):
            self.calls += 1
            return super().route(*args)
    maps = CountMaps(); state = {}; rounds = 0
    def completion(**kwargs):
        nonlocal rounds
        rounds += 1
        if rounds <= 2:
            return answer(calls=[call('get_group_routes', json.dumps({'members': MEMBERS, 'candidates': CANDIDATES}))])
        if rounds == 3:
            return answer(calls=[call('evaluate_meeting_fairness', json.dumps({'route_set_id': next(iter(state['route_sets']))}))])
        return answer('Union Square is best.')
    text, trace = run_agent([{'role': 'user', 'content': 'Compare these spots.'}], state, completion=completion, client=maps)
    assert maps.calls == len(MEMBERS) * len(CANDIDATES)
    assert len(state['route_sets']) == 1
    assert trace[1]['result']['reused'] is True
    assert trace[0]['result']['route_set_id'] == trace[1]['result']['route_set_id']
    assert trace[-1]['name'] == 'evaluate_meeting_fairness'
    assert text == 'Union Square is best.'
