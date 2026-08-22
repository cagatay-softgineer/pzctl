/* Resource trends: CPU, memory, disk and network over time.
 *
 * Charts are hand-drawn SVG rather than a charting library, because pzctl ships
 * no dependencies and the panel is served from the daemon itself - there is no
 * build step and nothing may be fetched from a CDN.
 *
 * Two conventions run through this file:
 *
 *  - A null reading is a *gap*, not a zero. The sampler reports null when a
 *    platform cannot answer or when a counter has not been read twice yet, and
 *    drawing that as zero would invent a CPU idle period that never happened.
 *    Every path is therefore built as a set of subpaths broken at nulls.
 *  - Each metric shows the server process against the whole machine where both
 *    exist. A server pinned at one core looks calm on a machine-wide graph, and
 *    a machine starved by something else looks calm on a process graph.
 */

(function () {
  "use strict";

  var WIDTH = 460;      // viewBox units; the SVG scales to its container
  var HEIGHT = 96;
  var PAD_TOP = 8;
  var PAD_BOTTOM = 14;

  var state = {
    window: 1800,
    data: null,
    hover: null,
    timer: null
  };

  // ── formatting ────────────────────────────────────────────

  function fmtPercent(v) {
    return v === null || v === undefined ? "--" : v.toFixed(v < 10 ? 1 : 0) + "%";
  }

  function fmtBytes(v) {
    if (v === null || v === undefined) return "--";
    var units = ["B", "KB", "MB", "GB", "TB"];
    var i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return (v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)) + " " + units[i];
  }

  function fmtRate(v) {
    return v === null || v === undefined ? "--" : fmtBytes(v) + "/s";
  }

  function fmtGB(mb) {
    return mb === null || mb === undefined ? "--" : (mb / 1024).toFixed(1) + " GB";
  }

  function fmtClock(epoch) {
    var d = new Date(epoch * 1000);
    return String(d.getHours()).padStart(2, "0") + ":" +
           String(d.getMinutes()).padStart(2, "0") + ":" +
           String(d.getSeconds()).padStart(2, "0");
  }

  // ── metric definitions ────────────────────────────────────
  //
  // `value` pulls the number a series plots out of one sample, so a series can
  // be a stored field or something derived (process memory as a share of the
  // machine, which is what makes it comparable with the machine-wide line).

  function processMemoryPercent(s) {
    if (!s.memory_process_mb || !s.memory_total_mb) return null;
    return s.memory_process_mb / s.memory_total_mb * 100;
  }

  var METRICS = [
    {
      key: "cpu",
      title: "CPU",
      fixedMax: 100,
      format: fmtPercent,
      series: [
        { name: "server", kind: "proc", value: function (s) { return s.cpu_process; } },
        { name: "machine", kind: "sys", value: function (s) { return s.cpu_system; } }
      ],
      headline: function (s) { return fmtPercent(s.cpu_process); },
      detail: function (s, meta) {
        return "machine " + fmtPercent(s.cpu_system) + " · " + meta.cpu_count + " cores";
      }
    },
    {
      key: "memory",
      title: "Memory",
      fixedMax: 100,
      format: fmtPercent,
      series: [
        { name: "server", kind: "proc", value: processMemoryPercent },
        { name: "machine", kind: "sys", value: function (s) { return s.memory_percent; } }
      ],
      headline: function (s) { return fmtGB(s.memory_process_mb); },
      detail: function (s) {
        return "machine " + fmtGB(s.memory_used_mb) + " / " + fmtGB(s.memory_total_mb);
      }
    },
    {
      key: "disk",
      title: "Disk",
      fixedMax: 100,
      format: fmtPercent,
      series: [
        { name: "used", kind: "proc", value: function (s) { return s.disk_percent; } }
      ],
      headline: function (s) {
        return s.disk_free_gb === null || s.disk_free_gb === undefined
          ? "--" : s.disk_free_gb.toFixed(1) + " GB";
      },
      detail: function (s) { return fmtPercent(s.disk_percent) + " used · free on save volume"; }
    },
    {
      key: "net",
      title: "Network",
      fixedMax: null,           // auto-scaled: traffic has no natural ceiling
      format: fmtRate,
      series: [
        { name: "in", kind: "proc", value: function (s) { return s.net_in_bps; } },
        { name: "out", kind: "sys", value: function (s) { return s.net_out_bps; } }
      ],
      headline: function (s) { return fmtRate(s.net_in_bps); },
      detail: function (s) { return "out " + fmtRate(s.net_out_bps); }
    }
  ];

  // ── chart drawing ─────────────────────────────────────────

  function scaleFor(metric, rows) {
    if (metric.fixedMax) return metric.fixedMax;
    var peak = 0;
    rows.forEach(function (row) {
      metric.series.forEach(function (s) {
        var v = s.value(row);
        if (v !== null && v !== undefined && v > peak) peak = v;
      });
    });
    // A little headroom so the peak never touches the top edge, and a floor so
    // an idle network does not scale noise up into a dramatic-looking graph.
    return Math.max(peak * 1.15, 1024);
  }

  function pointsFor(metric, series, rows, max) {
    var step = rows.length > 1 ? WIDTH / (rows.length - 1) : WIDTH;
    var usable = HEIGHT - PAD_TOP - PAD_BOTTOM;
    return rows.map(function (row, i) {
      var v = series.value(row);
      if (v === null || v === undefined) return null;
      var clamped = Math.max(0, Math.min(v, max));
      return {
        x: i * step,
        y: PAD_TOP + usable - (clamped / max) * usable
      };
    });
  }

  /* Build one <path> d-string per unbroken run of readings, so gaps stay gaps. */
  function linePath(points) {
    var out = [];
    var open = false;
    points.forEach(function (p) {
      if (!p) { open = false; return; }
      out.push((open ? "L" : "M") + p.x.toFixed(1) + " " + p.y.toFixed(1));
      open = true;
    });
    return out.join(" ");
  }

  function areaPath(points) {
    var out = [];
    var run = [];
    var flush = function () {
      if (run.length < 2) { run = []; return; }
      var base = HEIGHT - PAD_BOTTOM;
      var d = "M" + run[0].x.toFixed(1) + " " + base.toFixed(1);
      run.forEach(function (p) { d += " L" + p.x.toFixed(1) + " " + p.y.toFixed(1); });
      d += " L" + run[run.length - 1].x.toFixed(1) + " " + base.toFixed(1) + " Z";
      out.push(d);
      run = [];
    };
    points.forEach(function (p) { if (p) { run.push(p); } else { flush(); } });
    flush();
    return out.join(" ");
  }

  function svgFor(metric, rows) {
    if (!rows.length) {
      return '<svg class="res-chart" viewBox="0 0 ' + WIDTH + " " + HEIGHT +
             '" preserveAspectRatio="none"></svg>';
    }
    var max = scaleFor(metric, rows);
    var parts = [];
    var base = HEIGHT - PAD_BOTTOM;

    // Horizontal guides at quarter, half and three-quarter height.
    [0.25, 0.5, 0.75].forEach(function (f) {
      var y = (PAD_TOP + (base - PAD_TOP) * f).toFixed(1);
      parts.push('<line class="res-guide" x1="0" y1="' + y + '" x2="' + WIDTH + '" y2="' + y + '"/>');
    });
    parts.push('<line class="res-axis" x1="0" y1="' + base + '" x2="' + WIDTH + '" y2="' + base + '"/>');

    metric.series.slice().reverse().forEach(function (series) {
      var pts = pointsFor(metric, series, rows, max);
      var area = areaPath(pts);
      if (area) {
        parts.push('<path class="res-area k-' + series.kind + '" d="' + area +
                   '" fill="url(#grad-' + metric.key + "-" + series.kind + ')"/>');
      }
      var line = linePath(pts);
      if (line) parts.push('<path class="res-line k-' + series.kind + '" d="' + line + '"/>');
    });

    var defs = metric.series.map(function (series) {
      var id = "grad-" + metric.key + "-" + series.kind;
      return '<linearGradient id="' + id + '" x1="0" y1="0" x2="0" y2="1">' +
             '<stop class="g0 k-' + series.kind + '" offset="0"/>' +
             '<stop class="g1 k-' + series.kind + '" offset="1"/></linearGradient>';
    }).join("");

    return '<svg class="res-chart" viewBox="0 0 ' + WIDTH + " " + HEIGHT +
           '" preserveAspectRatio="none"><defs>' + defs + "</defs>" +
           parts.join("") +
           '<line class="res-cursor" x1="0" y1="' + PAD_TOP + '" x2="0" y2="' + base +
           '" style="display:none"/></svg>';
  }

  // ── rendering ─────────────────────────────────────────────

  function render() {
    var grid = document.getElementById("resGrid");
    if (!grid || !state.data) return;
    var data = state.data;
    var rows = data.history || [];
    var current = data.current || {};
    var meta = { cpu_count: data.cpu_count };

    grid.innerHTML = METRICS.map(function (metric) {
      var legend = metric.series.map(function (s) {
        return '<span class="res-key"><i class="k-' + s.kind + '"></i>' + s.name + "</span>";
      }).join("");
      return '<div class="res-metric" data-metric="' + metric.key + '">' +
               '<div class="res-top">' +
                 '<span class="res-name">' + metric.title + "</span>" +
                 '<span class="res-keys">' + legend + "</span>" +
               "</div>" +
               '<div class="res-read">' +
                 '<b class="res-val">' + metric.headline(current) + "</b>" +
                 '<span class="res-sub">' + metric.detail(current, meta) + "</span>" +
               "</div>" +
               '<div class="res-plot">' + svgFor(metric, rows) +
                 '<div class="res-tip"></div></div>' +
             "</div>";
    }).join("");

    bindHover(rows, meta);
  }

  /* One crosshair follows the pointer across whichever chart it is over.
   * The readout is the sample under the cursor, not the latest one, so the
   * graphs can be read historically rather than only as a live gauge. */
  function bindHover(rows, meta) {
    Array.prototype.forEach.call(document.querySelectorAll(".res-metric"), function (block) {
      var metric = METRICS.filter(function (m) {
        return m.key === block.getAttribute("data-metric");
      })[0];
      var plot = block.querySelector(".res-plot");
      var tip = block.querySelector(".res-tip");
      var cursor = block.querySelector(".res-cursor");
      var val = block.querySelector(".res-val");
      var sub = block.querySelector(".res-sub");

      function leave() {
        tip.classList.remove("on");
        if (cursor) cursor.style.display = "none";
        var current = state.data.current || {};
        val.textContent = metric.headline(current);
        sub.textContent = metric.detail(current, meta);
      }

      plot.addEventListener("mousemove", function (event) {
        if (!rows.length) return;
        var box = plot.getBoundingClientRect();
        var ratio = Math.max(0, Math.min((event.clientX - box.left) / box.width, 1));
        var index = Math.round(ratio * (rows.length - 1));
        var row = rows[index];
        if (!row) return;

        val.textContent = metric.headline(row);
        sub.textContent = metric.detail(row, meta);

        if (cursor) {
          var x = rows.length > 1 ? index * (WIDTH / (rows.length - 1)) : 0;
          cursor.setAttribute("x1", x);
          cursor.setAttribute("x2", x);
          cursor.style.display = "";
        }

        var lines = metric.series.map(function (s) {
          var v = s.value(row);
          return s.name + " " + (v === null || v === undefined ? "--" : metric.format(v));
        });
        tip.innerHTML = '<b>' + fmtClock(row.at) + "</b>" +
                        lines.map(function (l) { return "<span>" + l + "</span>"; }).join("");
        tip.style.left = Math.max(0, Math.min(ratio * box.width - 40, box.width - 96)) + "px";
        tip.classList.add("on");
      });
      plot.addEventListener("mouseleave", leave);
    });
  }

  // ── header tiles ──────────────────────────────────────────

  function paintHeader(current) {
    var cpu = document.getElementById("statCpu");
    var disk = document.getElementById("statDisk");
    if (cpu) {
      cpu.textContent = current.cpu_process === null || current.cpu_process === undefined
        ? "--" : fmtPercent(current.cpu_process);
    }
    if (disk) {
      disk.textContent = current.disk_free_gb === null || current.disk_free_gb === undefined
        ? "--" : current.disk_free_gb.toFixed(0) + " GB";
      disk.classList.toggle("warn", (current.disk_percent || 0) >= 90);
    }
  }

  // ── polling ───────────────────────────────────────────────

  async function refresh() {
    try {
      var data = await api("/api/sysres?window=" + state.window + "&points=180");
      if (!data.ok) {
        var hint = document.getElementById("resHint");
        if (hint && data.error) hint.textContent = data.error;
        return;
      }
      state.data = data;
      render();
      paintHeader(data.current || {});
    } catch (err) {
      /* A failed poll is not worth a toast - the next one is seconds away. */
    }
  }

  function bindWindows() {
    var box = document.getElementById("resWindows");
    if (!box) return;
    box.addEventListener("click", function (event) {
      var button = event.target.closest("[data-window]");
      if (!button) return;
      Array.prototype.forEach.call(box.querySelectorAll(".chip"), function (c) {
        c.classList.remove("on");
      });
      button.classList.add("on");
      state.window = parseInt(button.getAttribute("data-window"), 10);
      refresh();
    });
  }

  function init() {
    if (!document.getElementById("resGrid")) return;
    bindWindows();
    refresh();
    // Matches the sampler's own interval; polling faster only redraws the
    // same points.
    state.timer = setInterval(refresh, 5000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
