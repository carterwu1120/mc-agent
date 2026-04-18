from agent.skills.planner import _parse_decision_text                                                                                                        
                                                                                                                                                               
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