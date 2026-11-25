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
// The element gets data-plot-signals (how many ids it draws) and
// data-plot-range (the lowest and highest value in view) once points arrive,
// and data-plot-error with the reason when it can't get any.

const MIN_INTERVAL = 50; // ms between requests at most: 20 a second
const RETRY = 1000; // ms after a failed request
const TICKS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]; // s
// A heat map cell's strength, by how many spreads its value is from usual:
// faint up to USUAL (a wave's peaks are about 1.6), then rising to full
// color at FULL, where it also gets a mark big enough to see.
const USUAL = 2;
const FULL = 4;
const MARKS = 400; // at most, the strongest first

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

  let low = Infinity;
  let high = -Infinity;
  for (const series of plot.series.values()) {
    for (const point of series) {
      if (point == null) continue;
      const [a, b] = Array.isArray(point) ? point : [point, point];
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
  const tick = TICKS.find((s) => (s / seconds) * width >= 70) ?? seconds;
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
  g.globalAlpha = 0.7;
  for (let ago = 0; ago <= seconds; ago += tick) {
    const at = x(plot.end - ago);
    g.textAlign = ago === 0 ? "right" : at < 30 ? "left" : "center";
    g.fillText(ago === 0 ? "now" : `−${describe(ago)}`, at, lines + axis / 2);
  }
  if (heat) return;

  // The lines, as one path: each point from its lowest to its highest value.
  g.globalAlpha = 1;
  g.lineWidth = 1;
  g.lineJoin = "round";
  g.save();
  g.beginPath();
  g.rect(0, 0, width, lines); // the newest point may run past "now"
  g.clip();
  g.beginPath();
  for (const series of plot.series.values()) {
    let pen = false;
    for (let i = 0; i < series.length; i++) {
      const point = series[i];
      if (point == null) {
        pen = false;
        continue;
      }
      const at = x(plot.start + (i + 0.5) * plot.step);
      const [a, b] = Array.isArray(point) ? point : [point, point];
      if (pen) g.lineTo(at, y(a));
      else g.moveTo(at, y(a));
      if (b !== a) g.lineTo(at, y(b));
      pen = true;
    }
  }
  g.stroke();
  g.restore();

  // The lowest and highest value, over the lines at full strength, bold and
  // outlined in the page's background, so no line can hide them.
  g.font = `bold ${style.fontSize} ${style.fontFamily}`;
  g.textAlign = "left";
  g.lineWidth = Math.max(parseFloat(style.fontSize) / 4, 3);
  g.strokeStyle = background(el);
  for (const [text, baseline, at] of [
    [format(high), "top", 2],
    [format(low), "bottom", lines - 2],
  ]) {
    g.textBaseline = baseline;
    g.strokeText(text, 3, at);
    g.fillText(text, 3, at);
  }
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
