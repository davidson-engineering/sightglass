import pytest

from sightglass import (
    Coordinate,
    Counters,
    IndicatorLamp,
    LogMonitor,
    MachineState,
    Monitor,
    ProgressBar,
    RangeBar,
    Sparkline,
    Style,
    Table,
    TextElement,
    TextFormat,
)
from sightglass.formatting import visible_len


def test_label_defaults_to_last_id_segment():
    assert TextElement("X.velocity").label == "velocity"
    assert TextElement("speed", label="Speed").label == "Speed"


def test_empty_id_is_rejected():
    with pytest.raises(ValueError):
        TextElement("")


def test_text_element_formats_value_not_caption():
    text = TextElement("x", caption="X: ", units="mm", format=TextFormat(precision=2))
    text.update("1.5")
    assert text.render(40) == "X: 1.50 mm"
    assert text.to_json() == "1.50 mm"


def test_text_element_scale():
    text = TextElement("x", scale=0.01, format=TextFormat(precision=1))
    text.update("250")
    assert text.to_json() == "2.5"
    with pytest.raises(ValueError):
        text.update("abc")


def test_progress_bar():
    bar = ProgressBar("p", total=10, label="Loading")
    bar.update(5)
    line = bar.render(40)
    assert line.startswith(f"{'Loading':<12} [")
    assert "█" in line and "░" in line and line.endswith(" 50.0%")
    assert len(line) == 40
    assert bar.to_json() == {"text": "50.0%", "ratio": 0.5}
    bar.update(99)
    assert bar.ratio == 1.0


def test_range_bar_shows_actual_value_but_clamps_marker():
    bar = RangeBar("r", min=-10, max=10, units="mm", format=TextFormat(precision=1))
    assert bar.to_json() == {"text": "- mm", "ratio": None}  # no data yet
    assert "|" not in bar.render(50)
    bar.update(25)
    assert bar.to_json() == {"text": "25.0 mm", "ratio": 1.0}
    line = bar.render(50)
    assert line.index("|") == line.index("]") - 1  # marker pinned at the right end
    assert len(line) == 50


def test_range_bar_scale():
    bar = RangeBar("r", scale=60 / 6000)
    bar.update(3000)
    assert bar.value == 30


def test_table_cells_are_fields():
    table = Table("t", rows=["a", "b"], columns=["x", "y"], cell_width=5)
    table.update_field("a.x", 100)
    table.update({"b": {"y": 200}})
    assert table.to_json() == {"a": {"x": "100", "y": ""}, "b": {"x": "", "y": "200"}}
    lines = table.render(60).split("\n")
    assert lines[0] == "┌───┬───────┬───────┐"
    assert lines[3] == "│ a │  100  │       │"
    assert len({len(line) for line in lines}) == 1
    with pytest.raises(KeyError):
        table.update_field("c.x", 1)


def test_log_monitor_keeps_latest_lines():
    log = LogMonitor("log", lines=3)
    for message in ["one", "two", "three", "four"]:
        log.update(message)
    assert log.to_json() == "two\nthree\nfour"
    assert log.render(10).split("\n") == ["two       ", "three     ", "four      "]


def test_log_monitor_timestamp():
    log = LogMonitor("log", timestamp=True, timestamp_format="[%Y]")
    log.update("hi")
    assert log.to_json().endswith("] hi")


@pytest.mark.parametrize(
    ("value", "on"),
    [
        (True, True),
        (1, True),
        ("1", True),
        ("true", True),
        ("ON", True),
        (False, False),
        (0, False),
        ("0", False),
        ("false", False),
        ("off", False),
    ],
)
def test_indicator_lamp_parses_device_booleans(value, on):
    lamp = IndicatorLamp("lamp", label="Pump")
    lamp.update(value)
    assert lamp.to_json() is on
    color = "32" if on else "31"
    assert lamp.render(20) == f"{'Pump':<12} \x1b[1;{color}m●\x1b[0m"


def test_indicator_lamp_rejects_garbage():
    with pytest.raises(ValueError):
        IndicatorLamp("lamp").update("maybe")


def test_machine_state_bits():
    state = MachineState("m", states=["a", "b", "c"])
    state.update(5)
    assert state.to_json() == {"a": True, "b": False, "c": True}
    state.update("0b010")
    assert state.to_json() == {"a": False, "b": True, "c": False}
    state.update_field("c", "1")
    assert state.bits == 0b110
    with pytest.raises(KeyError):
        state.update_field("d", 1)


def test_machine_state_render_wraps_to_width():
    state = MachineState("m", states=["deadman_switch", "motors_enabled", "estop"])
    lines = state.render(50).split("\n")
    assert len(lines) == 2
    assert all(visible_len(line) <= 50 for line in lines)
    assert lines[0].startswith(f"{'m':<12} ")
    assert lines[1].startswith(" " * 13)  # wrapped lamps stay in the value column


def test_coordinate():
    pos = Coordinate("pos", label="Position", units="mm")
    assert pos.to_json() == {"x": "-", "y": "-", "z": "-"}
    pos.update([1, -2, 3.5])
    pos.update_field("x", 0.25)
    assert pos.to_json() == {
        "x": "+0.2500 mm",
        "y": "-2.0000 mm",
        "z": "+3.5000 mm",
    }
    assert pos.render(80) == "Position X: +0.2500 mm Y: -2.0000 mm Z: +3.5000 mm"
    with pytest.raises(ValueError):
        pos.update([1, 2])


def test_counters():
    jobs = Counters("jobs.totals", ["submitted", "done", "failed"], label="Jobs")
    assert jobs.to_json() == {"submitted": "-", "done": "-", "failed": "-"}
    jobs.update({"submitted": 44, "done": 41})
    jobs.update_field("failed", 2)
    assert jobs.to_json() == {"submitted": "44", "done": "41", "failed": "2"}
    assert jobs.render(80) == "Jobs submitted 44  done 41  failed 2"
    assert jobs.describe()["fields"] == ["submitted", "done", "failed"]
    with pytest.raises(KeyError):
        jobs.update_field("lost", 1)
    with pytest.raises(KeyError):
        jobs.update({"lost": 1})


def test_counters_format_overrides_the_default():
    rate = Counters("r", ["a"], format=TextFormat(precision=1))
    rate.update_field("a", 3)
    assert rate.to_json() == {"a": "3.0"}


def test_counters_through_a_monitor():
    monitor = Monitor()
    monitor.add(Counters("jobs.totals", ["done", "failed"]))
    monitor.set("jobs.totals.done", 41)
    assert monitor["jobs.totals"].to_json() == {"done": "41", "failed": "-"}


def test_copy_is_independent():
    original = RangeBar("velocity")
    clone = original.copy("X.velocity")
    clone.update(5)
    assert (clone.id, clone.label, clone.value) == ("X.velocity", "velocity", 5)
    assert original.value is None


def test_range_bars_with_different_units_line_up():
    speed = RangeBar("v", units="mm/s")
    torque = RangeBar("t", units="Nm")
    for bar in (speed, torque):
        bar.update(50)
    assert speed.render(50).index("]") == torque.render(50).index("]")
    assert len(speed.render(50)) == len(torque.render(50)) == 50


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (18.627682319492283, "18.6277"),  # long floats are shortened
        (0.1 + 0.2, "0.3"),
        (1.5, "1.5"),
        ("1.50", "1.50"),  # a device's own formatting is kept
        (1234567.891, "1234568"),
        (7, "7"),
        ("OK", "OK"),
    ],
)
def test_text_without_a_format_is_readable(value, shown):
    text = TextElement("x")
    text.update(value)
    assert text.to_json() == shown


def test_sparkline_keeps_recent_history():
    spark = Sparkline("rate", points=3, units="/s", format=TextFormat(precision=1))
    assert spark.to_json() == {"text": "-", "values": []}
    for value in [1, 2, "3", 4]:
        spark.update(value)
    assert spark.to_json() == {"text": "4.0 /s", "values": [2.0, 3.0, 4.0]}
    line = spark.render(30)
    assert line.startswith(f"{'rate':<12} ▁▅█")
    assert line.endswith(" 4.0 /s")
    with pytest.raises(ValueError):
        spark.update("fast")
    for _ in range(3):
        spark.update(7)
    assert spark.render(30).startswith(f"{'rate':<12} ▄▄▄")  # steady, not zero


def test_sparkline_interval_keeps_one_point_per_interval(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("sightglass.elements.monotonic", lambda: now[0])
    spark = Sparkline("altitude", points=3, interval=1)
    for t, value in [(0, 1), (0.4, 2), (0.9, 3), (1.0, 4), (1.5, 5), (2.2, 6)]:
        now[0] = t
        spark.update(value)
    # Updates within an interval replace its point: the chart covers
    # points x interval seconds, and its last point is the latest value.
    assert spark.to_json() == {"text": "6", "values": [3, 5, 6]}
    with pytest.raises(ValueError, match="interval"):
        Sparkline("altitude", interval=0)


def test_sparkline_scale():
    spark = Sparkline(
        "net", units="KB/s", scale=1 / 1024, format=TextFormat(precision=1)
    )
    spark.update(2048)
    assert spark.to_json() == {"text": "2.0 KB/s", "values": [2.0]}


def test_range_bar_takes_a_text_format_like_other_elements():
    bar = RangeBar("r", min=-1, max=1, format=TextFormat(precision=1, force_sign=True))
    bar.update(0.25)
    assert bar.to_json() == {"text": "+0.2", "ratio": 0.625}


def test_lamps_take_styles_like_other_elements():
    lit = Style(fg=214)
    lamp = IndicatorLamp("pump", on_style=lit)
    lamp.update(True)
    assert lamp.render(20).endswith(lit.apply("●"))


def test_sparkline_keeps_whole_numbers_whole():
    for value in [36, "36"]:
        spark = Sparkline("queue")
        spark.update(value)
        assert spark.to_json() == {"text": "36", "values": [36]}
    spark = Sparkline("net", scale=1 / 1024)
    spark.update(1000)
    assert spark.text == "0.976562"
    text = TextElement("load")
    text.update(0.0205078)
    assert text.to_json() == "0.0205078"
