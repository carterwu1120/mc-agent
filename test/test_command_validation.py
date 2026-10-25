import pytest

from agent.handlers.command_validation import validate_command


@pytest.mark.parametrize('command', ['explore animals', 'explore trees', 'stopexplore'])
def test_removed_exploration_is_rejected(command):
    assert validate_command(command) is not None


@pytest.mark.parametrize('command', ['tp 10 64 -200', 'tp -5.5 70 3', 'tp Carter', 'tp Carter_2'])
def test_tp_accepts_coordinates_or_player(command):
    assert validate_command(command) is None


@pytest.mark.parametrize('command', ['tp', 'tp 10 64', 'tp 10', 'tp Carter 64', 'tp some-player', 'tp ' + 'a' * 17])
def test_tp_rejects_other_shapes(command):
    assert validate_command(command) is not None
