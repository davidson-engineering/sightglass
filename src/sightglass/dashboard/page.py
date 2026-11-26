"""The page a dashboard makes: plain HTML in the style of ``dashboard.css``."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from .panels import COLOURS, WEIGHT, draw, span

if TYPE_CHECKING:
    from . import Dashboard, Section


def render(dashboard: Dashboard) -> str:
    """The page: sections of panels, plain HTML that ``sightglass.js`` and
    ``plot.js`` draw into. It opens the live-values connection only if a
    panel shows live values; plots read history."""
    from .. import __version__

    title = html.escape(dashboard.title)
    identity = [
        f"v{dashboard.version}" if dashboard.version else "",
        dashboard.config,
        f"config {dashboard.digest}" if dashboard.digest else "",
        f"sightglass {__version__}",
        f"started {dashboard.started}" if dashboard.started else "",
    ]
    meta = html.escape(" · ".join(part for part in identity if part))
    shared = {p.ranges for p in dashboard.panels if p.type == "trend"}
    key = ""
    if len(shared) == 1:  # a legend only when every plot has the same ranges
        parts = (
            f'<b class="range-{i % COLOURS}">{span(r)}</b>'
            for i, r in enumerate(next(iter(shared)))
        )
        key = f"<span>top to bottom: {' &middot; '.join(parts)}</span>"
    live = "" if any(p.live for p in dashboard.panels) else ' data-values="off"'
    sections = "\n".join(_section(s, dashboard.panel_width) for s in dashboard.sections)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="/_sightglass/dashboard.css">
</head>
<body>
  <header>
    <h1>{title}</h1>
    <small class="identity">{meta}</small>
    {key}
  </header>
  <main>
{sections}
  </main>
  <script src="/_sightglass/sightglass.js"{live}></script>
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
