"""The panel types of a config file (trend, value, lamp, gauge) and its
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
    assert series.type == "trend"  # the default type
    assert [p.seconds for p in series.plots] == [300, 60]


def test_only_a_trend_needs_history_and_only_the_others_need_live_values(tmp_path):
    found = dashboard(tmp_path, ALL)
    assert found.plotted() == ["plc.pressure"]
    assert 'data-values="off"' not in found.page()  # a gauge reads live values
    plots = dashboard(tmp_path, '[[panel]]\nids = ["a.*"]')
    assert 'data-values="off"' in plots.page()


def test_a_number_shown_to_fixed_decimals_is_declared_once(tmp_path):
    text = (
        '[[panel]]\ntype = "value"\nid = "a"\nprecision = 2\n'
        '[[panel]]\ntype = "gauge"\nid = "a"\nprecision = 2\n'
        '[[panel]]\ntype = "value"\nid = "b"\n'
    )
    (element,) = dashboard(tmp_path, text).elements()
    assert isinstance(element, TextElement) and element.id == "a"
    assert element.format.precision == 2


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
        ('[[panel]]\ntype = "pie"\nids = ["a"]', "type in .* must be trend"),
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


def test_large_numbers_are_written_whole(tmp_path):
    text = (
        '[[panel]]\ntype = "gauge"\nid = "a"\nmax = 1234567\n'
        '[[panel]]\nids = ["b"]\nrange = 1234567\n'
    )
    page = dashboard(tmp_path, text).page()
    assert "--max: 1234567" in page and 'data-seconds="1234567"' in page


def test_two_panels_may_not_show_an_id_to_different_decimals(tmp_path):
    text = (
        '[[panel]]\ntype = "value"\nid = "a"\nprecision = 1\n'
        '[[panel]]\ntype = "gauge"\nid = "a"\nprecision = 3\n'
    )
    with pytest.raises(ValueError, match="a is shown to 1 and 3 decimals"):
        dashboard(tmp_path, text)
    same = text.replace("precision = 3", "precision = 1")
    assert len(dashboard(tmp_path, same).elements()) == 1


def test_a_config_that_is_not_utf8_names_the_file(tmp_path):
    path = tmp_path / "dashboard.toml"
    path.write_bytes('title = "caf\u00e9"'.encode("utf-16"))
    with pytest.raises(ValueError, match=r"dashboard\.toml: not UTF-8") as error:
        load(path)
    assert "\n" not in str(error.value)


def test_version_is_text_or_a_number(tmp_path):
    path = tmp_path / "dashboard.toml"
    body = SOURCE + '[[panel]]\nids = ["a"]\n'
    path.write_text("version = true\n" + body, "utf-8")
    with pytest.raises(ValueError, match="title and version"):
        load(path)
    path.write_text("version = 2\n" + body, "utf-8")
    assert load(path).version == "2"


def plots(panel):
    return [(p.role, p.seconds, p.grow) for p in panel.plots]


def trend(tmp_path, keys=""):
    found = dashboard(tmp_path, f'[[panel]]\nids = ["a"]\n{keys}')
    return found.panels[0]


def test_a_trend_has_a_primary_and_a_secondary_plot_the_secondary_on_top(tmp_path):
    assert plots(trend(tmp_path)) == [("secondary", 300, 0.5), ("primary", 60, 1)]


def test_a_trend_is_set_with_its_ranges_ratio_and_order(tmp_path):
    keys = (
        "range = 120\nsecondary_range = 900\nsecondary_ratio = 0.25\n"
        'first = "primary"\n'
    )
    assert plots(trend(tmp_path, keys)) == [
        ("primary", 120, 1),
        ("secondary", 900, 0.25),
    ]
    # the secondary may cover less time than the primary
    assert plots(trend(tmp_path, "range = 300\nsecondary_range = 30")) == [
        ("secondary", 30, 0.5),
        ("primary", 300, 1),
    ]


def test_a_trend_may_have_no_secondary_plot(tmp_path):
    panel = trend(tmp_path, "secondary = false\nrange = 90")
    assert plots(panel) == [("primary", 90, 1)]
    page = dashboard(tmp_path, '[[panel]]\nids = ["a"]\nsecondary = false').page()
    assert page.count('class="plot ') == 1 and "secondary" not in page


def test_history_covers_the_longest_plot_of_either_role(tmp_path):
    found = dashboard(tmp_path, '[[panel]]\nids = ["a"]\nsecondary_range = 900')
    assert found.history == 960
    found = dashboard(tmp_path, '[[panel]]\nids = ["a"]\nsecondary = false')
    assert found.history == 120  # the primary's minute, and a minute


def test_the_page_draws_the_plots_in_the_order_and_ratio_asked_for(tmp_path):
    keys = 'first = "primary"\nsecondary_ratio = 0.25'
    page = dashboard(tmp_path, f'[[panel]]\nids = ["a"]\n{keys}').page()
    assert page.index('plot primary" style="--grow: 1"') < page.index(
        'plot secondary" style="--grow: 0.25"'
    )


@pytest.mark.parametrize(
    ("keys", "message"),
    [
        ("secondary = 1", "secondary in .* true or false"),
        ("range = 0", "range in .* above 0"),
        ("secondary_range = -5", "secondary_range in .* above 0"),
        ("secondary_ratio = 0", "secondary_ratio in .* above 0"),
        ('secondary_ratio = "half"', "secondary_ratio in .* above 0"),
        ('first = "top"', "first in .* secondary or primary, not 'top'"),
        (
            'secondary = false\nfirst = "primary"',
            "first in .* go with secondary = true",
        ),
        (
            "secondary = false\nsecondary_range = 60\nsecondary_ratio = 1",
            "secondary_range, secondary_ratio in .* go with secondary = true",
        ),
        ("ranges = [300, 60]", "unknown 'ranges'"),
    ],
)
def test_a_bad_trend_setting_says_what_is_wrong_in_one_line(tmp_path, keys, message):
    with pytest.raises(ValueError, match=message) as error:
        trend(tmp_path, keys)
    assert "\n" not in str(error.value)


def test_the_title_bar_is_at_the_top_by_default_and_can_go_to_the_bottom(tmp_path):
    def page(setting):
        path = tmp_path / "dashboard.toml"
        path.write_text(f"{setting}{SOURCE}\n[[panel]]\nids = ['a']\n", "utf-8")
        return load(path).page()

    top = page("")
    assert top.index("<header>") < top.index("<main>")
    explicit = page('title_bar = "top"\n')
    assert explicit.index("<header>") < explicit.index("<main>")
    bottom = page('title_bar = "bottom"\n')
    assert bottom.index("</main>") < bottom.index("<header>")
    assert bottom.count("<header>") == 1  # moved, not copied
    with pytest.raises(ValueError, match="title_bar must be top or bottom, not 'side'"):
        page('title_bar = "side"\n')


def test_the_page_has_no_legend_and_its_identity_is_in_the_title_bar(tmp_path):
    page = dashboard(tmp_path, '[[panel]]\nids = ["a"]').page()
    assert "top to bottom" not in page and "<span>" not in page
    header = page[page.index("<header>") : page.index("</header>")]
    assert 'class="identity"' in header
