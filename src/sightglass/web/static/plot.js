// Value-vs-time plots, loaded by sightglass.js on pages that have one:
//
//   <div data-plot="wave.*" data-seconds="60"></div>
//
// draws the ids named in data-plot (space-separated; * matches any
// characters) over the last data-seconds seconds (default 60), from the
// dashboard's history (GET /history: start it with --history). Give the
// element a height; the lines and labels are its CSS color and font.
//
// The plot loads its window once, at about one point per pixel, then asks
// only for the points since its newest one, as often as a point lasts
// (20 times a second at most). Each point is drawn from its lowest to its
// highest value, so a spike shorter than a point still shows. Times come
// from the dashboard's clock, so a viewer's wrong clock doesn't matter.
//
// With data-plot-mode="heatmap" it draws one row per id instead, a cell per
// point, for more ids than lines can show (a whole PLC, say), to find the
// ones doing something unusual. A cell's strength is how far its value is
// from its id's usual value (the median in view), in units of the id's usual
// spread (the mean distance from that): faint up to two spreads, the
// element's full color at four or more, where the cell also gets a mark at
// least 7 px tall, so a glitch one cell wide can't be missed. A steady wave
// stays faint, and a glitch, a step or a drift shows, up or down; a point
// that spans two values shows the one further away. With more ids than pixel
// rows, each pixel shows the strongest of the cells it covers. Lines show
// the values.
//
// The value axis has a label at round values (data-plot-ticks lists them),
// each with data-plot-units after it ("MB"); a value of 10,000 or more is
// shortened with k, M or G. data-plot-scale multiplies the values first, so
// a plot of bytes can be shown in MB (data-plot-scale="0.00000095367431640625").
//
// Two to eight ids are drawn each in its own colour, in a fixed order whose
// steps suit the plot's background (light or dark), with a legend naming
// them (data-plot-legend), less the part of the name they share. One id, or
// more than eight, are all drawn in the element's color: a crowd of lines is
// read as one shape, and nine colours can't all be told apart.
//
// The element gets data-plot-signals (how many ids it draws) and
// data-plot-range (the lowest and highest value in view, scaled) once points
// arrive, and data-plot-error with the reason when it can't get any.

const MIN_INTERVAL = 50; // ms between requests at most: 20 a second
const RETRY = 1000; // ms after a failed request
const TICKS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]; // s
// A heat map cell's strength, by how many spreads its value is from usual:
// faint up to USUAL (a wave's peaks are about 1.6), then rising to full
// color at FULL, where it also gets a mark big enough to see.
const USUAL = 2;
const FULL = 4;
const MARKS = 400; // at most, the strongest first
const TICK_ROOM = 3; // the value axis's labels: at least this many lines of text apart
const STEPS = [1, 2, 2.5, 5, 10]; // round steps between them, times a power of ten
const PREFIXES = ["", "k", "M", "G", "T", "P"];
// The colours of 2 to 8 ids, in this order: [on a light background, on a
// dark one]. Validated for colour-blind and normal vision, adjacent pairs.
const PALETTE = [
  ["#257de0", "#338ced"], // blue
  ["#f47230", "#e76624"], // orange
  ["#00b894", "#00a888"], // aqua
  ["#f2a600", "#d28e05"], // yellow
  ["#ee7fac", "#e24d8b"], // magenta
  ["#008a17", "#0aac23"], // green
  ["#6a4ce2", "#9887ef"], // violet
  ["#ec4647", "#eb6263"], // red
];

const running = new WeakSet();

export function start(el) {
  if (running.has(el)) return;
  running.add(el);
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvas.style.display = "block";
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  el.replaceChildren(canvas);
  const plot = {
    el,
    canvas,
    ids: el.dataset.plot.trim().split(/\s+/),
    seconds: 0,
    width: 0,
    start: 0, // Unix time the first point begins
    step: 0, // seconds per point; 0 until the window is loaded
    end: 0, // the dashboard's time at its latest answer
    series: new Map(), // id -> points (a number, [low, high] or null)
    error: null,
  };
  new ResizeObserver(() => draw(plot)).observe(el);
  poll(plot);
}

async function poll(plot) {
  const started = performance.now();
  let interval = MIN_INTERVAL;
  if (document.hidden) {
    interval = 500; // nothing to see: ask again when it is
  } else {
    try {
      await fetchPoints(plot);
      document.dispatchEvent(
        new CustomEvent("monitor:plot", { detail: { took: performance.now() - started } }),
      );
      interval = Math.min(Math.max(plot.step * 1000, MIN_INTERVAL), 1000);
    } catch (error) {
      plot.error = error.message; // the dashboard has gone: keep what we have
      interval = RETRY;
    }
    if (plot.error) plot.el.dataset.plotError = plot.error;
    else delete plot.el.dataset.plotError;
    draw(plot);
  }
  // Once a point, counted from this request's start, not its end.
  setTimeout(() => poll(plot), Math.max(interval - (performance.now() - started), 0));
}

async function fetchPoints(plot) {
  const seconds = Number(plot.el.dataset.seconds) || 60;
  const width = Math.max(Math.round(plot.el.clientWidth), 2);
  const params = new URLSearchParams();
  for (const id of plot.ids) params.append("id", id);
  const reload = !plot.step || seconds !== plot.seconds || width !== plot.width;
  if (reload) {
    params.set("seconds", seconds);
    const heat = plot.el.dataset.plotMode === "heatmap"; // cells of 2 px
    params.set("points", Math.min(heat ? Math.ceil(width / 2) : width, 10000));
  } else {
    // From the newest point, which was still filling: it comes back complete.
    params.set("from", plot.start + (pointCount(plot) - 1) * plot.step);
    params.set("step", plot.step);
  }
  const response = await fetch(`/history?${params}`);
  if (!response.ok) {
    plot.error = (await response.text()).trim() || response.statusText;
    plot.step = 0;
    return;
  }
  const result = await response.json();
  plot.error = null;
  plot.end = result.start + result.points * result.step;
  if (reload) {
    Object.assign(plot, { seconds, width, start: result.start, step: result.step });
    plot.series = new Map();
  }
  // Points sit at multiples of the step; round away the clocks' drift.
  const offset = Math.round((result.start - plot.start) / plot.step);
  for (const [id, points] of Object.entries(result.signals)) {
    if (points === null) continue; // no history (yet)
    let series = plot.series.get(id);
    if (!series) plot.series.set(id, (series = []));
    for (let i = 0; i < points.length; i++) series[offset + i] = points[i];
  }
  // Forget what has scrolled out of the window.
  const gone = Math.floor((plot.end - seconds - plot.start) / plot.step) - 1;
  if (gone > 0) {
    for (const series of plot.series.values()) series.splice(0, gone);
    plot.start += gone * plot.step;
  }
  plot.el.dataset.plotSignals = String(plot.series.size);
}

function pointCount(plot) {
  let count = 0;
  for (const series of plot.series.values()) count = Math.max(count, series.length);
  return count;
}

function draw(plot) {
  const { el, canvas } = plot;
  const ratio = window.devicePixelRatio || 1;
  const width = el.clientWidth;
  const height = el.clientHeight;
  if (canvas.width !== Math.round(width * ratio)) canvas.width = Math.round(width * ratio);
  if (canvas.height !== Math.round(height * ratio)) {
    canvas.height = Math.round(height * ratio);
  }
  const g = canvas.getContext("2d");
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.globalAlpha = 1; // the context keeps the last draw's settings
  g.clearRect(0, 0, width, height);
  const style = getComputedStyle(el);
  g.fillStyle = g.strokeStyle = style.color;
  g.font = `${style.fontSize} ${style.fontFamily}`;
  const axis = Math.round(parseFloat(style.fontSize) * 1.6); // the time labels
  g.textBaseline = "middle";

  const scale = Number(el.dataset.plotScale) || 1;
  const ends = (point) => {
    const [a, b] = Array.isArray(point) ? point : [point, point];
    return scale > 0 ? [a * scale, b * scale] : [b * scale, a * scale];
  };
  let low = Infinity;
  let high = -Infinity;
  for (const series of plot.series.values()) {
    for (const point of series) {
      if (point == null) continue;
      const [a, b] = ends(point);
      if (a < low) low = a;
      if (b > high) high = b;
    }
  }
  const seconds = plot.seconds || Number(el.dataset.seconds) || 60;
  canvas.setAttribute(
    "aria-label",
    `${plot.series.size} signals over the last ${describe(seconds)}`,
  );
  if (low === Infinity) {
    g.textAlign = "center";
    g.globalAlpha = 0.7;
    g.fillText(plot.error ?? "Waiting for values", width / 2, height / 2, width - 16);
    return;
  }
  el.dataset.plotRange = `${format(low)} ${format(high)}`;
  if (low === high) {
    low -= 1;
    high += 1;
  }
  const lines = height - axis;
  const pad = (high - low) * 0.04;
  const y = (v) => lines - ((v - low + pad) / (high - low + 2 * pad)) * lines;
  const from = plot.end - seconds;
  const x = (t) => ((t - from) / seconds) * width;
  const heat = el.dataset.plotMode === "heatmap";
  if (heat) drawHeat(g, plot, from, seconds, width, lines, ratio, style.color);

  // The time axis: a line every few seconds, labelled back from now.
  // Labels need room: at least 70 px apart, more for bigger text.
  const gap = Math.max(70, parseFloat(style.fontSize) * 4.5);
  const tick = TICKS.find((s) => (s / seconds) * width >= gap) ?? seconds;
  const minutes = tick % 60 === 0; // one unit for every label on the axis
  g.globalAlpha = 0.22;
  g.lineWidth = 1;
  g.beginPath();
  for (let ago = 0; ago <= seconds; ago += tick) {
    const at = Math.round(x(plot.end - ago)) - 0.5;
    g.moveTo(at, 0);
    g.lineTo(at, lines);
  }
  g.moveTo(0, lines + 0.5);
  g.lineTo(width, lines + 0.5);
  g.stroke();
  const ticks = heat ? null : valueTicks(low, high, pad, lines, parseFloat(style.fontSize));
  if (ticks) {
    g.globalAlpha = 0.12;
    g.beginPath();
    for (const tick of ticks.values) {
      const at = Math.round(y(tick)) + 0.5;
      g.moveTo(0, at);
      g.lineTo(width, at);
    }
    g.stroke();
    el.dataset.plotTicks = ticks.values.map((v) => ticks.text(v)).join(" ");
  }
  g.globalAlpha = 0.7;
  for (let ago = 0; ago <= seconds; ago += tick) {
    const at = x(plot.end - ago);
    g.textAlign = ago === 0 ? "right" : at < 30 ? "left" : "center";
    const label = minutes ? `${ago / 60} min` : `${ago} s`;
    g.fillText(ago === 0 ? "now" : `−${label}`, at, lines + axis / 2);
  }
  if (heat) return;

  // The lines: each point from its lowest to its highest value. A few ids
  // get a colour each; otherwise they are one path in the element's color.
  const ids = [...plot.series.keys()];
  const colours = ids.length >= 2 && ids.length <= PALETTE.length;
  const step = dark(background(el)) ? 1 : 0;
  g.globalAlpha = 1;
  g.lineWidth = colours ? 2 : 1;
  g.lineJoin = "round";
  g.save();
  g.beginPath();
  g.rect(0, 0, width, lines); // the newest point may run past "now"
  g.clip();
  g.beginPath();
  ids.forEach((id, n) => {
    const series = plot.series.get(id);
    let pen = false;
    for (let i = 0; i < series.length; i++) {
      const point = series[i];
      if (point == null) {
        pen = false;
        continue;
      }
      const at = x(plot.start + (i + 0.5) * plot.step);
      const [a, b] = ends(point);
      if (pen) g.lineTo(at, y(a));
      else g.moveTo(at, y(a));
      if (b !== a) g.lineTo(at, y(b));
      pen = true;
    }
    if (colours) {
      g.strokeStyle = PALETTE[n][step];
      g.stroke();
      g.beginPath();
    }
  });
  if (!colours) g.stroke();
  g.restore();

  // Over the lines, outlined in the page's background so no line hides them:
  // the value labels at the left, then the legend at the top right.
  const size = parseFloat(style.fontSize);
  g.lineWidth = Math.max(size / 4, 3);
  g.strokeStyle = background(el);
  g.fillStyle = style.color;
  g.textAlign = "left";
  g.textBaseline = "middle";
  const units = el.dataset.plotUnits ?? "";
  for (const tick of ticks.values) {
    const text = ticks.label(tick, units);
    const at = Math.min(Math.max(y(tick), size * 0.6), lines - size * 0.6);
    g.strokeText(text, 3, at);
    g.fillText(text, 3, at);
  }
  if (colours) legend(g, el, ids, step, width, size);
  else delete el.dataset.plotLegend;
}

// Round values for the value axis, about TICK_ROOM lines of text apart, all
// written with the same decimals: [1.5, 2.0, 2.5], or [50M, 100M].
function valueTicks(low, high, pad, lines, size) {
  const wanted = Math.max(Math.floor(lines / (size * TICK_ROOM)), 2);
  const raw = (high - low) / wanted;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = STEPS.map((s) => s * power).find((s) => s >= raw * (1 - 1e-9));
  const values = [];
  for (let k = Math.ceil((low - pad) / step); k * step <= high + pad; k++) {
    values.push(Math.abs(k) < 1e-9 ? 0 : k * step);
  }
  const biggest = Math.max(...values.map(Math.abs));
  const thousands = biggest >= 1e4 ? Math.min(Math.floor(Math.log10(biggest) / 3), 5) : 0;
  const divisor = 1000 ** thousands;
  const prefix = PREFIXES[thousands];
  let decimals = 0;
  const scaled = step / divisor;
  while (decimals < 10 && Math.abs(Math.round(scaled * 10 ** decimals) - scaled * 10 ** decimals) > 1e-6) {
    decimals++;
  }
  const number = (v) => (v / divisor).toFixed(decimals).replace("-", "−");
  return {
    values,
    text: (v) => number(v) + prefix,
    label: (v, units) =>
      units ? `${number(v)} ${prefix}${units}` : number(v) + prefix,
  };
}

// The legend: a swatch and a name per id, at the top right, the names less
// the dotted part they all share ("jobs.queue jobs.p95" -> "queue", "p95").
// Ids that don't fit are counted: "+3".
function legend(g, el, ids, step, width, size) {
  const names = shortNames(ids);
  el.dataset.plotLegend = names.join(" ");
  const swatch = size;
  const gap = size * 0.4;
  const widths = names.map((name) => swatch + gap + g.measureText(name).width);
  let shown = names.length;
  const total = (count) =>
    widths.slice(0, count).reduce((sum, w) => sum + w + size, 0) +
    (count < names.length ? g.measureText(`+${names.length - count}`).width : 0);
  while (shown > 1 && total(shown) > width * 0.7) shown--;
  let at = width - total(shown); // each name's trailing gap is the right margin
  const middle = size * 0.9;
  for (let n = 0; n < shown; n++) {
    g.fillStyle = PALETTE[n][step];
    g.fillRect(at, middle - 1.5, swatch, 3);
    at += swatch + gap;
    g.fillStyle = getComputedStyle(el).color;
    g.strokeText(names[n], at, middle);
    g.fillText(names[n], at, middle);
    at += widths[n] - swatch - gap + size;
  }
  if (shown < names.length) {
    const more = `+${names.length - shown}`;
    g.strokeText(more, at, middle);
    g.fillText(more, at, middle);
  }
}

function shortNames(ids) {
  const parts = ids.map((id) => id.split("."));
  let shared = 0;
  while (
    parts.every((p) => p.length > shared + 1 && p[shared] === parts[0][shared])
  ) {
    shared++;
  }
  return parts.map((p) => p.slice(shared).join("."));
}

// Whether a CSS color is dark enough for the palette's dark steps.
function dark(color) {
  const [r, g, b] = rgb(color).map((c) => {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.18;
}

function drawHeat(g, plot, from, seconds, width, lines, ratio, color) {
  const all = [...plot.series.values()];
  const rows = Math.max(Math.min(all.length, Math.round(lines * ratio)), 1);
  const columns = Math.max(Math.ceil(seconds / plot.step), 1);
  const offset = Math.round((plot.start - from) / plot.step); // column of point 0
  const [red, green, blue] = rgb(color);
  const image = new ImageData(columns, rows);
  const cells = image.data;
  const marks = []; // [spreads away, row, column] of cells at full color
  for (let s = 0; s < all.length; s++) {
    const series = all[s];
    const usual = typical(series);
    if (usual === null) continue;
    const [middle, spread] = usual;
    const row = Math.floor((s * rows) / all.length);
    for (let i = 0; i < series.length; i++) {
      const point = series[i];
      const column = offset + i;
      if (point == null || column < 0 || column >= columns) continue;
      const [a, b] = Array.isArray(point) ? point : [point, point];
      const away = Math.max(Math.abs(a - middle), Math.abs(b - middle)) / spread;
      const alpha = Math.round(255 * strength(away));
      if (away >= FULL) marks.push([away, row, column]);
      const at = (row * columns + column) * 4;
      if (alpha <= cells[at + 3]) continue; // the strongest cell wins the pixel
      cells[at] = red;
      cells[at + 1] = green;
      cells[at + 2] = blue;
      cells[at + 3] = alpha;
    }
  }
  plot.bitmap ??= document.createElement("canvas");
  plot.bitmap.width = columns;
  plot.bitmap.height = rows;
  plot.bitmap.getContext("2d").putImageData(image, 0, 0);
  g.save();
  g.globalAlpha = 1;
  g.imageSmoothingEnabled = false; // crisp cells, a spike stays one cell
  g.drawImage(plot.bitmap, 0, 0, width, lines);
  // Marks over the cells at full color, outlined in the page's background
  // (the canvas is transparent, so the element's own background shows).
  const cellWidth = width / columns;
  const cellHeight = lines / rows;
  const markWidth = Math.max(cellWidth, 3);
  const markHeight = Math.max(cellHeight, 7);
  marks.sort((x, y) => y[0] - x[0]);
  g.lineWidth = 1.5;
  g.strokeStyle = background(plot.el);
  for (const [, row, column] of marks.slice(0, MARKS)) {
    const x = column * cellWidth + cellWidth / 2 - markWidth / 2;
    const y = Math.min(
      Math.max(row * cellHeight + cellHeight / 2 - markHeight / 2, 0),
      lines - markHeight,
    );
    g.strokeRect(x, y, markWidth, markHeight);
    g.fillRect(x, y, markWidth, markHeight);
  }
  g.restore();
}

// A cell's opacity, 0 to 1, for a value ``away`` spreads from usual.
function strength(away) {
  if (away < USUAL) return 0.03 + (0.09 * away) / USUAL;
  return 0.12 + 0.88 * Math.min((away - USUAL) / (FULL - USUAL), 1);
}

// The color behind an element: its own background, or its nearest
// ancestor's that has one; white if none does.
function background(el) {
  for (let node = el; node; node = node.parentElement) {
    const color = getComputedStyle(node).backgroundColor;
    if (rgb(color).length && !/rgba\(.*,\s*0\)$|transparent/.test(color)) return color;
  }
  return "white";
}

// A series' usual value and spread: the median of the values its points
// reach (both ends of a point that spans two), and their mean distance from
// it (not the median distance, which is 0 for a signal that is at one of
// two values over half the time; never 0, so any change from a steady value
// shows at full strength). Null for no numbers.
function typical(series) {
  const reached = [];
  for (const point of series) {
    if (point == null) continue;
    if (Array.isArray(point)) reached.push(point[0], point[1]);
    else reached.push(point);
  }
  if (!reached.length) return null;
  const median = (values) => {
    values.sort((x, y) => x - y);
    const half = values.length >> 1;
    return values.length % 2 ? values[half] : (values[half - 1] + values[half]) / 2;
  };
  const middle = median(reached);
  let spread = 0;
  for (const value of reached) spread += Math.abs(value - middle);
  spread /= reached.length;
  return [middle, spread || Math.abs(middle) * 1e-9 || 1e-12];
}

// A CSS color as [red, green, blue], whatever form getComputedStyle gives.
function rgb(color) {
  const swatch = new OffscreenCanvas(1, 1).getContext("2d");
  swatch.fillStyle = color;
  swatch.fillRect(0, 0, 1, 1);
  return [...swatch.getImageData(0, 0, 1, 1).data.slice(0, 3)];
}

function describe(seconds) {
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}

function format(value) {
  return String(Number(value.toPrecision(4))).replace("-", "−"); // as the time axis
}
