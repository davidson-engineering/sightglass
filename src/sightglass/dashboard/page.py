"""The page a dashboard makes: plain HTML in the style of ``dashboard.css``."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from .panels import WEIGHT, draw

if TYPE_CHECKING:
    from . import Dashboard, Section


def render(dashboard: Dashboard) -> str:
    """The page: sections of panels, plain HTML that ``sightglass.js`` and
    ``plot.js`` draw into. It opens the live-values connection only if a
    panel shows live values; plots read history."""
    from .. import __version__

    title = html.escape(dashboard.title)
    identity = [
        f"v{dashboard.version}"
        if dashboard.version[:1].isdigit()
        else dashboard.version,
        dashboard.config,
        f"config {dashboard.digest}" if dashboard.digest else "",
        f"sightglass {__version__}",
        f"started {dashboard.started}" if dashboard.started else "",
    ]
    meta = html.escape(" · ".join(part for part in identity if part))
    bar = (
        f"  <header>\n    <h1>{title}</h1>\n"
        f'    <small class="identity">{meta}</small>\n  </header>\n'
    )
    live = "" if any(p.live for p in dashboard.panels) else ' data-values="off"'
    sections = "\n".join(_section(s, dashboard.panel_width) for s in dashboard.sections)
    above, below = (bar, "") if dashboard.title_bar == "top" else ("", bar)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="/_sightglass/dashboard.css">
</head>
<body>
{above}  <main>
{sections}
  </main>
{below}  <script src="/_sightglass/sightglass.js"{live}></script>
</body>
</html>
"""


def _section(section: Section, panel_width: int) -> str:
    weight = max(WEIGHT.get(p.type, 1) for p in section.panels)
    heading = f"    <h2>{html.escape(section.title)}</h2>\n" if section.title else ""
    panels = "\n".join(draw(panel) for panel in section.panels)
    return (
        f'  <section style="--weight: {weight}">\n{heading}'
        f'    <div class="panels" style="--panel-width: {panel_width}px">\n'
        f"{panels}\n    </div>\n  </section>"
    )
