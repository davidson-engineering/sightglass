"""The panel types of a config file (timeseries, value, lamp, gauge) and its
sections."""

import pytest

from sightglass.dashboard import load
from sightglass.elements import TextElement

SOURCE = """
[[source]]
url = "http://127.0.0.1:1/x"
"""


def dashboard(tmp_path, text):
    path = tmp_path / "dashboard.toml"
    path.write_text(SOURCE + text, "utf-8")
    return load(path)


ALL = """
[[section]]
title = "Overview"

[[section.panel]]
type = "gauge"
title = "Pressure"
id = "plc.pressure"
units = "bar"
precision = 1
min = 0
max = 10

[[section.panel]]
type = "value"
title = "Count"
id = "plc.count"

[[section.panel]]
type = "lamp"
title = "Running"
id = "plc.running"

[[section]]
title = "History"

[[section.panel]]
title = "Pressure"
ids = ["plc.pressure"]
"""


def test_every_type_reads_its_own_keys(tmp_path):
    found = dashboard(tmp_path, ALL)
    assert [s.title for s in found.sections] == ["Overview", "History"]
    gauge, value, lamp, series = found.panels
    assert (gauge.type, gauge.ids, gauge.units, gauge.precision) == (
        "gauge",
        ("plc.pressure",),
        "bar",
        1,
    )
    assert (gauge.low, gauge.high) == (0.0, 10.0)
    assert (value.type, value.precision, value.units) == ("value", None, "")
    assert lamp.type == "lamp"
    assert (series.type, series.ranges) == ("timeseries", (300, 60))  # the default type


def test_only_a_timeseries_needs_history_and_only_the_others_need_live_values(tmp_path):
    found = dashboard(tmp_path, ALL)
    assert found.plotted() == ["plc.pressure"]
    assert 'data-values="off"' not in found.page()  # a gauge reads live values
    plots = dashboard(tmp_path, '[[panel]]\nids = ["a.*"]')
    assert 'data-values="off"' in plots.page()


def test_a_number_shown_to_fixed_decimals_is_declared_once(tmp_path):
    text = (
        '[[panel]]\ntype = "value"\nid = "a"\nprecision = 2\n'
        '[[panel]]\ntype = "gauge"\nid = "a"\nprecision = 0\n'
        '[[panel]]\ntype = "value"\nid = "b"\n'
    )
    (element,) = dashboard(tmp_path, text).elements()
    assert isinstance(element, TextElement) and element.id == "a"
    assert element.format.precision == 2  # the first panel to name it decides


def test_each_makes_a_panel_per_name_of_any_type(tmp_path):
    text = (
        '[[panel]]\ntype = "lamp"\neach = ["A", "B"]\n'
        'title = "Pump {name}"\nid = "plc.pump{name}.on"\n'
    )
    found = dashboard(tmp_path, text)
    assert [(p.title, p.ids) for p in found.panels] == [
        ("Pump A", ("plc.pumpA.on",)),
        ("Pump B", ("plc.pumpB.on",)),
    ]


def test_the_page_has_a_heading_per_section_and_an_article_per_panel(tmp_path):
    page = dashboard(tmp_path, ALL).page()
    assert page.count("<section") == 2 and "<h2>Overview</h2>" in page
    assert page.count("<article") == 4
    assert 'class="panel gauge"' in page and 'class="panel lamp"' in page
    assert '<span data-bind="plc.pressure"></span><small>bar</small>' in page
    assert 'class="light" data-bind="plc.running" data-mode="state"' in page
    assert 'class="fill" data-bind="plc.pressure" data-mode="var"' in page
    assert "--min: 0; --max: 10" in page
    assert '--weight: 1"' in page and '--weight: 3"' in page  # readings are short


def test_panels_at_the_top_level_are_one_section_without_a_heading(tmp_path):
    page = dashboard(tmp_path, '[[panel]]\nids = ["a"]').page()
    assert page.count("<section") == 1 and "<h2>" not in page


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[[panel]]\ntype = "pie"\nids = ["a"]', "type in .* must be timeseries"),
        ('[[panel]]\ntype = "value"\nid = "a"\nranges = [5]', "unknown 'ranges'"),
        ('[[panel]]\ntype = "value"\nids = ["a"]', "unknown 'ids'"),
        ('[[panel]]\nid = "a"', "unknown 'id'"),
        ('[[panel]]\ntype = "value"', "needs an id"),
        ('[[panel]]\ntype = "lamp"\nid = "a.*"', "names one id"),
        ('[[panel]]\ntype = "value"\nid = "a"\nprecision = -1', "precision"),
        ('[[panel]]\ntype = "value"\nid = "a"\nprecision = 1.5', "precision"),
        ('[[panel]]\ntype = "value"\nid = "a"\nunits = 5', "units"),
        ('[[panel]]\ntype = "gauge"\nid = "a"\nmin = 5\nmax = 5', "min .* below max"),
        ('[[panel]]\ntype = "gauge"\nid = "a"\nmin = "0"', "min and max"),
        (
            '[[panel]]\nids = ["a"]\n[[section]]\ntitle = "x"\n[[section.panel]]\n'
            'ids = ["b"]',
            "in a .*section.* or none",
        ),
        ('[[section]]\ntitle = "x"', "needs at least one .*section.panel"),
        ('[[section]]\ncolour = 1\n[[section.panel]]\nids = ["b"]', "unknown 'colour'"),
        ('[[section]]\ntitle = 1\n[[section.panel]]\nids = ["b"]', "title in"),
    ],
)
def test_a_bad_panel_says_what_is_wrong_in_one_line(tmp_path, text, message):
    with pytest.raises(ValueError, match=message) as error:
        dashboard(tmp_path, text)
    assert "\n" not in str(error.value)
