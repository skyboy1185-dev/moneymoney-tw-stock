from app.services.day_trading_strategies import (
    route_signals_to_active_robot,
    strategy_context,
    strategy_eligible_signals,
)


def _regime(score: int, data_status: str = "normal") -> dict[str, object]:
    return {
        "score": score,
        "dataStatus": data_status,
        "mode": "official",
    }


def _session(phase: str = "scanning", allowed: bool = True) -> dict[str, object]:
    return {"phase": phase, "formalSignalsAllowed": allowed}


def test_market_score_selects_the_expected_strategy_robot() -> None:
    expected = [
        (75, "strong-bull-breakout", "long"),
        (35, "bull-pullback", "long"),
        (0, "range-two-way", "both"),
        (-35, "bear-rebound-short", "short"),
        (-75, "strong-bear-breakdown", "short"),
    ]
    for score, robot_id, direction in expected:
        result = strategy_context(_regime(score), _session())
        assert result["activeRobot"]["id"] == robot_id
        assert result["activeRobot"]["direction"] == direction
        assert len(result["strategyRobots"]) == 5
        assert sum(robot["selected"] for robot in result["strategyRobots"]) == 1


def test_strategy_confidence_is_degraded_when_quotes_are_unhealthy() -> None:
    healthy = strategy_context(_regime(80), _session())["activeRobot"]
    delayed = strategy_context(_regime(80, "severe_delay"), _session(allowed=False))["activeRobot"]

    assert healthy["confidence"] > delayed["confidence"]
    assert delayed["confidence"] <= 30
    assert delayed["status"] == "paused"


def test_entry_cutoff_keeps_robot_visible_in_position_management_mode() -> None:
    robot = strategy_context(_regime(-40), _session("entry_closed", False))["activeRobot"]

    assert robot["id"] == "bear-rebound-short"
    assert robot["status"] == "managing"


def test_directional_robot_only_routes_aligned_formal_signals() -> None:
    robot = strategy_context(_regime(40), _session())["activeRobot"]
    routed = route_signals_to_active_robot([
        {"id": "long", "direction": "long"},
        {"id": "short", "direction": "short"},
    ], robot)

    assert [signal["id"] for signal in strategy_eligible_signals(routed)] == ["long"]
    assert all(signal["strategyRobotName"] == "多頭回撤機器人" for signal in routed)


def test_range_robot_keeps_general_long_and_short_signals() -> None:
    robot = strategy_context(_regime(0), _session())["activeRobot"]
    routed = route_signals_to_active_robot([
        {"id": "long", "direction": "long"},
        {"id": "short", "direction": "short"},
    ], robot)

    assert [signal["id"] for signal in strategy_eligible_signals(routed)] == ["long", "short"]


def test_afternoon_robot_status_explains_long_only_policy() -> None:
    long_robot = strategy_context(
        _regime(40), _session("long_only", True),
    )["activeRobot"]
    short_robot = strategy_context(
        _regime(-40), _session("long_only", True),
    )["activeRobot"]

    assert long_robot["status"] == "active"
    assert long_robot["statusLabel"] == "午後僅允許多方進場"
    assert short_robot["status"] == "paused"
    assert "空方 11:00 已截止" in short_robot["statusLabel"]
