from agent.skills.llm_response import parse_llm_json


def test_reasoning_stripped_from_output(capsys):
    result = parse_llm_json({"action": "plan", "commands": ["mine iron 3"], "reasoning": "缺鐵鎬"}, "Test")
    assert "reasoning" not in result
    assert result["action"] == "plan"
    assert "推理" in capsys.readouterr().out


def test_extra_fields_preserved():
    result = parse_llm_json({"action": "drop", "items": ["diorite", "tuff"]}, "Test")
    assert result["items"] == ["diorite", "tuff"]
    assert result["action"] == "drop"


def test_validation_fallback_on_bad_commands():
    # commands must be list[str]; a plain string triggers ValidationError → fallback
    raw = {"action": "plan", "commands": "not_a_list"}
    result = parse_llm_json(raw, "Test")
    assert result is raw  # fallback returns original dict unchanged


def test_no_reasoning_no_extra_keys():
    result = parse_llm_json({"action": "continue"}, "Test")
    assert result == {"action": "continue"}
