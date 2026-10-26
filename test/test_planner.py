from agent.handlers.planner import _parse_decision_text                                                                                                        
                                                                                                                                                               
def test_plan_with_reasoning():
    r = _parse_decision_text('{"action":"plan","goal":"挖鐵","reasoning":"缺鐵鎬","commands":["mine iron 3"]}')
    assert r["action"] == "plan"
    assert r["commands"] == ["mine iron 3"]
    assert "reasoning" not in r  # reasoning is printed and stripped, not returned                                                                                                                    
                                                                                                                                                            
def test_plan_no_reasoning_excluded():
    r = _parse_decision_text('{"action":"plan","goal":"挖鐵","commands":["mine iron 3"]}')                                                                   
    assert "reasoning" not in r                                                                                                                              

def test_chat():                                                                                                                                             
    r = _parse_decision_text('{"action":"chat","text":"好的"}')                                                                                            
    assert r["action"] == "chat"
    assert r["text"] == "好的"

def test_armor_request_ends_with_makearmor_not_a_bare_equip(monkeypatch):
    # `equip` never crafts, so "make iron armor" used to gather iron and stop there.
    from agent.handlers import planner
    from agent.handlers.command_validation import validate_commands
    monkeypatch.setattr(planner.task_memory, 'load', lambda: None)
    state = {'activity': 'idle', 'inventory': [{'name': 'cooked_beef', 'count': 10}, {'name': 'iron_ingot', 'count': 4}],
             'equipment': {'armor': {}}}
    plan = planner._equipment_goal_shortcut('幫我做一套鐵裝', state, 'idle')
    assert plan['commands'] == ['mine iron 20', 'smelt raw_iron 20', 'makearmor iron']
    assert validate_commands(plan['commands']) == []
    assert validate_commands(['makearmor gold'])
