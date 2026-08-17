"use strict";

/* ── token + api ───────────────────────────────────────────── */

let TOKEN = (window.PZ_TOKEN && window.PZ_TOKEN !== "__PZCTL_" + "TOKEN__")
  ? window.PZ_TOKEN
  : (localStorage.getItem("pzctl_token") || "");

if (TOKEN) localStorage.setItem("pzctl_token", TOKEN);

function askToken() {
  const value = prompt("pzctl access token (printed in the daemon console):", TOKEN || "");
  if (value) { TOKEN = value.trim(); localStorage.setItem("pzctl_token", TOKEN); location.reload(); }
}

async function api(path, options) {
  const opts = Object.assign({ headers: {} }, options || {});
  opts.headers["X-PZ-Token"] = TOKEN;
  if (opts.body !== undefined && typeof opts.body !== "string") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
    opts.method = opts.method || "POST";
  }
  const res = await fetch(path, opts);
  if (res.status === 401) { askToken(); throw new Error("unauthorized"); }
  const data = await res.json().catch(() => ({ ok: false, error: "bad response" }));
  if (!res.ok && !data.error) data.error = "HTTP " + res.status;
  return data;
}

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function toast(message, bad) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), bad ? 7000 : 3800);
}

function post(path, body) {
  return api(path, { method: "POST", body: body || {} }).then((r) => {
    if (r.ok) { if (r.message) toast(r.message); }
    else toast(r.error || r.message || "failed", true);
    return r;
  });
}

/* ── tabs ──────────────────────────────────────────────────── */

const LOADERS = {};
$$(".tab").forEach((tab) => tab.addEventListener("click", () => {
  $$(".tab").forEach((t) => t.classList.toggle("on", t === tab));
  $$(".panel").forEach((p) => p.classList.toggle("on", p.id === "tab-" + tab.dataset.tab));
  const load = LOADERS[tab.dataset.tab];
  if (load) load();
}));
$$("[data-reload]").forEach((btn) =>
  btn.addEventListener("click", () => LOADERS[btn.dataset.reload] && LOADERS[btn.dataset.reload]()));

/* ── status ────────────────────────────────────────────────── */

function fmtDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  const d = Math.floor(seconds / 86400), h = Math.floor(seconds % 86400 / 3600);
  const m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
  if (d) return d + "d " + h + "h";
  if (h) return h + "h " + m + "m";
  if (m) return m + "m " + s + "s";
  return s + "s";
}

const ACTIVE_STATES = ["running", "starting", "stopping"];

async function refreshStatus() {
  let st;
  try { st = await api("/api/status"); } catch (e) { return; }
  if (!st.ok) return;

  const pill = $("#statePill");
  pill.className = "pill " + st.state;
  $("#stateText").textContent = st.state + (st.note ? " — " + st.note : "");
  $("#statUptime").textContent = st.pid ? fmtDuration(st.uptime_sec) : "--";
  $("#statPlayers").textContent = st.state === "running" ? String(st.players) : "--";
  $("#statMem").textContent = st.memory_mb ? (st.memory_mb / 1024).toFixed(1) + " / " + (st.xmx || "?") : "--";
  $("#statRcon").textContent = st.rcon_ready ? "ready" : "off";
  // Only ever set from the server, so what is shown is what is running.
  if (st.version && !$("#pzVersion").textContent) $("#pzVersion").textContent = "v" + st.version;

  const alive = ACTIVE_STATES.includes(st.state);
  $('[data-act="start"]').disabled = alive || st.state === "backoff";
  $('[data-act="stop"]').disabled = !alive;
  $('[data-act="kill"]').disabled = !alive;
  $('[data-act="restart"]').disabled = st.state === "stopping";

  const players = $("#playerList");
  players.innerHTML = "";
  if (st.player_names && st.player_names.length) {
    st.player_names.forEach((name) => {
      const li = document.createElement("li");
      li.className = "playerrow";
      const label = document.createElement("span");
      label.textContent = name;
      li.appendChild(label);
      const actions = document.createElement("span");
      actions.className = "playeracts";
      actions.appendChild(modButton("kick", name));
      actions.appendChild(modButton("ban", name));
      li.appendChild(actions);
      players.appendChild(li);
    });
  } else {
    players.textContent = st.state === "running" ? "nobody online" : "server offline";
  }

  const sched = $("#scheduleList");
  sched.innerHTML = "";
  if (st.schedule && st.schedule.length) {
    st.schedule.forEach((ev) => {
      const li = document.createElement("li");
      li.textContent = ev.kind + " @ " + ev.time;
      const small = document.createElement("small");
      small.textContent = "in " + fmtDuration(ev.in_sec);
      li.appendChild(small);
      sched.appendChild(li);
    });
  } else {
    sched.textContent = "nothing scheduled";
  }

  const paths = $("#pathList");
  paths.innerHTML = "";
  Object.entries(st.paths || {}).forEach(([key, value]) => {
    const dt = document.createElement("dt"); dt.textContent = key;
    const dd = document.createElement("dd"); dd.textContent = value;
    paths.append(dt, dd);
  });
}

$$("[data-act]").forEach((btn) => btn.addEventListener("click", async () => {
  const act = btn.dataset.act;
  if (act === "kill" && !confirm("Kill the server process? The world is NOT saved first.")) return;
  if (act === "stop" && !confirm("Stop the server? Players will be disconnected.")) return;
  btn.disabled = true;
  await post("/api/server/" + act);
  refreshStatus();
}));

/* ── console ───────────────────────────────────────────────── */

const consoleEl = $("#console");
const MAX_LINES = 2000;

function appendLine(record) {
  const div = document.createElement("div");
  div.className = "s-" + record.s;
  const time = document.createElement("span");
  time.className = "t";
  time.textContent = new Date(record.t * 1000).toLocaleTimeString();
  div.append(time, document.createTextNode(record.line));
  consoleEl.appendChild(div);
  while (consoleEl.childElementCount > MAX_LINES) consoleEl.removeChild(consoleEl.firstChild);
  if ($("#autoscroll").checked) consoleEl.scrollTop = consoleEl.scrollHeight;
}

async function loadHistory() {
  const data = await api("/api/console?limit=600");
  consoleEl.innerHTML = "";
  (data.lines || []).forEach(appendLine);
}

let stream = null;
function openStream() {
  if (stream) stream.close();
  stream = new EventSource("/api/console/stream?token=" + encodeURIComponent(TOKEN));
  stream.onmessage = (ev) => appendLine(JSON.parse(ev.data));
  stream.onerror = () => { stream.close(); stream = null; setTimeout(openStream, 3000); };
}

$("#clearConsole").addEventListener("click", () => { consoleEl.innerHTML = ""; });
$("#cmdForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = $("#cmdInput");
  const cmd = input.value.trim();
  if (!cmd) return;
  input.value = "";
  const res = await api("/api/command", { body: { cmd } });
  if (!res.ok) toast(res.error || res.message, true);
});
$$(".quickcmds .chip").forEach((chip) => chip.addEventListener("click", () => {
  $("#cmdInput").value = chip.dataset.cmd;
  $("#cmdInput").focus();
}));

/* ── option editors (INI + Sandbox) ────────────────────────── */

const NUMERIC = /^-?\d+(\.\d+)?$/;
const isBoolish = (v) => typeof v === "boolean" || v === "true" || v === "false";
const truthy = (v) => v === true || v === "true";
const sameValue = (a, b) =>
  (isBoolish(a) || isBoolish(b)) ? truthy(a) === truthy(b) : String(a) === String(b);

// Decide how a set of enum labels maps onto the stored value.
//   "index"   -> SandboxVars store 1-based ints ("Zombies = 4" means choice 4)
//   "literal" -> the INI stores the choice text itself ("AntiCheatHit=kick")
function enumMode(value, choices) {
  if (!choices || !choices.length) return null;
  if (typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= choices.length) {
    return "index";
  }
  if (choices.some((c) => c.toLowerCase() === String(value).toLowerCase())) return "literal";
  return null;
}

function buildControl(value, meta, onInput) {
  const choices = meta.choices || [];
  const mode = enumMode(value, choices);
  let el, read;

  if (mode) {
    el = document.createElement("select");
    choices.forEach((label, i) => {
      const opt = document.createElement("option");
      opt.value = mode === "index" ? String(i + 1) : label;
      opt.textContent = mode === "index" ? `${i + 1} — ${label}` : label;
      el.appendChild(opt);
    });
    const match = mode === "index"
      ? String(value)
      : (choices.find((c) => c.toLowerCase() === String(value).toLowerCase()) || String(value));
    el.value = match;
    read = () => (mode === "index" ? Number(el.value) : el.value);
  } else if (isBoolish(value)) {
    el = document.createElement("input");
    el.type = "checkbox";
    el.checked = truthy(value);
    read = () => el.checked;
  } else if (meta.multiline) {
    el = document.createElement("textarea");
    el.rows = 2;
    el.spellcheck = false;
    el.value = String(value);
    read = () => el.value;
  } else if (typeof value === "number" || NUMERIC.test(String(value))) {
    el = document.createElement("input");
    el.type = "number";
    el.step = String(value).includes(".") ? "0.05" : "1";
    el.value = String(value);
    read = () => el.value;
  } else {
    el = document.createElement("input");
    el.type = "text";
    el.value = String(value);
    read = () => el.value;
  }

  el.addEventListener("input", () => onInput(read()));
  el.addEventListener("change", () => onInput(read()));
  const set = (v) => {
    if (el.type === "checkbox") el.checked = truthy(v);
    else if (mode === "index") el.value = String(v);
    else el.value = String(v);
    onInput(read());
  };
  return { el, read, set, mode };
}

// Plain-language description of what the stored value currently means.
function explainValue(value, meta) {
  const mode = enumMode(value, meta.choices);
  if (mode === "index") return `${value} = ${meta.choices[value - 1]}`;
  if (isBoolish(value)) return truthy(value) ? "on" : "off";
  return "";
}

function makeEditor(opts) {
  const state = { original: {}, meta: {}, changes: {}, controls: {} };

  const markDirty = () => {
    const n = Object.keys(state.changes).length;
    opts.dirtyEl.textContent = n ? n + " unsaved" : "";
  };

  function optionCard(key, value) {
    const meta = state.meta[key] || { label: key, tooltip: "", choices: [] };
    const card = document.createElement("div");
    card.className = "opt";
    card.dataset.key = key;
    card.dataset.search = (meta.label + " " + key + " " + (meta.tooltip || "")).toLowerCase();

    const top = document.createElement("div");
    top.className = "opt-top";
    const name = document.createElement("span");
    name.className = "opt-label";
    name.textContent = meta.label;
    const raw = document.createElement("code");
    raw.className = "opt-key";
    raw.textContent = key;
    const flag = document.createElement("span");
    flag.className = "opt-flag";
    top.append(name, raw, flag);

    const ctlWrap = document.createElement("div");
    ctlWrap.className = "opt-ctl";

    const meaning = document.createElement("span");
    meaning.className = "opt-meaning";

    const foot = document.createElement("p");
    foot.className = "opt-meta";

    const refreshNotes = (current) => {
      meaning.textContent = explainValue(current, meta);
      const bits = [];
      if (meta.hint) bits.push(meta.hint);
      if (meta.default !== undefined) {
        const def = meta.default;
        const shown = enumMode(def, meta.choices) === "index" ? meta.choices[def - 1] : String(def);
        bits.push(sameValue(def, current) ? `default (${shown})` : `default: ${shown}`);
      }
      foot.textContent = bits.join("  ·  ");
      const offDefault = meta.default !== undefined && !sameValue(meta.default, current);
      card.classList.toggle("off-default", offDefault);
      if (offDefault && resetBtn) foot.appendChild(resetBtn);
    };

    let resetBtn = null;
    if (meta.default !== undefined) {
      resetBtn = document.createElement("button");
      resetBtn.className = "reset";
      resetBtn.type = "button";
      resetBtn.textContent = "reset";
      resetBtn.title = "restore the preset default";
      resetBtn.addEventListener("click", () => control.set(meta.default));
    }

    const control = buildControl(value, meta, (current) => {
      const same = sameValue(state.original[key], current);
      if (same) delete state.changes[key];
      else state.changes[key] = current;
      card.classList.toggle("changed", !same);
      flag.textContent = same ? "" : "modified";
      refreshNotes(current);
      markDirty();
      applyFilter();
    });
    state.controls[key] = control;

    ctlWrap.append(control.el, meaning);
    card.append(top, ctlWrap);

    if (meta.tooltip) {
      const desc = document.createElement("p");
      desc.className = "opt-desc";
      desc.textContent = meta.tooltip;
      desc.title = "click to expand";
      desc.addEventListener("click", () => desc.classList.toggle("open"));
      card.appendChild(desc);
    }
    card.appendChild(foot);
    refreshNotes(value);
    return card;
  }

  function applyFilter() {
    const needle = opts.searchEl.value.trim().toLowerCase();
    const modifiedOnly = opts.modifiedEl && opts.modifiedEl.checked;
    const nonDefaultOnly = opts.nonDefaultEl && opts.nonDefaultEl.checked;
    let shown = 0;
    $$("#" + opts.fieldsEl.id + " .opt").forEach((card) => {
      let ok = !needle || card.dataset.search.includes(needle);
      if (ok && modifiedOnly) ok = card.classList.contains("changed");
      if (ok && nonDefaultOnly) ok = card.classList.contains("off-default");
      card.hidden = !ok;
      if (ok) shown++;
    });
    $$("#" + opts.fieldsEl.id + " .grp").forEach((grp) => {
      const visible = grp.querySelectorAll(".opt:not([hidden])").length;
      grp.hidden = visible === 0;
      const badge = grp.querySelector(".grp-count");
      if (badge) badge.textContent = visible;
      if (needle && visible) grp.open = true;
    });
    if (opts.countEl) {
      const total = Object.keys(state.original).length;
      opts.countEl.textContent = shown === total ? `${total} settings` : `${shown} of ${total} shown`;
    }
  }

  async function load() {
    const data = await api(opts.endpoint);
    if (!data.ok) return toast(data.error || "load failed", true);
    state.original = data.values || {};
    state.meta = data.meta || {};
    state.changes = {};
    state.controls = {};
    opts.fieldsEl.innerHTML = "";
    opts.navEl.innerHTML = "";
    markDirty();

    opts.pathEl.textContent = data.path +
      (data.exists ? "" : "   (not generated yet — start the server once)") +
      (data.preset ? `   ·  defaults shown from the ${data.preset} preset` : "");

    const keys = Object.keys(state.original);
    if (!keys.length) {
      opts.fieldsEl.innerHTML = "<p class='hint'>nothing to show yet</p>";
      if (opts.countEl) opts.countEl.textContent = "";
      return;
    }

    // Bucket by group, then emit in the server's declared group order.
    const buckets = {};
    keys.forEach((key) => {
      const group = (state.meta[key] && state.meta[key].group) || "Other";
      (buckets[group] = buckets[group] || []).push(key);
    });
    const order = (data.groups || []).filter((g) => buckets[g]);
    Object.keys(buckets).forEach((g) => { if (!order.includes(g)) order.push(g); });

    order.forEach((group) => {
      const details = document.createElement("details");
      details.className = "grp";
      details.open = true;
      details.id = opts.fieldsEl.id + "-" + group.replace(/\W+/g, "-");

      const summary = document.createElement("summary");
      const title = document.createElement("span");
      title.textContent = group;
      const count = document.createElement("span");
      count.className = "grp-count";
      count.textContent = buckets[group].length;
      summary.append(title, count);

      const body = document.createElement("div");
      body.className = "opts";
      buckets[group].forEach((key) => body.appendChild(optionCard(key, state.original[key])));

      details.append(summary, body);
      opts.fieldsEl.appendChild(details);

      const chip = document.createElement("button");
      chip.className = "chip";
      chip.textContent = `${group} ${buckets[group].length}`;
      chip.addEventListener("click", () => {
        details.open = true;
        details.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      opts.navEl.appendChild(chip);
    });
    applyFilter();
  }

  async function save(applyLive) {
    if (!Object.keys(state.changes).length) return toast("nothing to save");
    const res = await api(opts.endpoint, {
      body: { changes: state.changes, apply_live: !!applyLive },
    });
    if (!res.ok) return toast(res.error || "save failed", true);

    const count = (res.changed || []).length;
    if (!res.live) {
      toast("saved " + count + " setting(s) — restart the server to apply");
    } else if (!res.live.ok && res.live.error) {
      // The file was still written; only the live push failed.
      toast("saved " + count + " setting(s), but live apply failed: " + res.live.error, true);
    } else {
      toast(describeLive(res.live, count), !res.live.ok);
    }
    load();
  }

  // Summarise what the server actually accepted. pzctl keeps no whitelist of
  // live-applicable options, so this reports the outcome rather than promising
  // it in advance.
  function describeLive(live, count) {
    const parts = ["saved " + count + " setting(s)"];
    if (live.applied.length) parts.push(live.applied.length + " applied live");
    if (live.restart_required.length) {
      parts.push("restart needed for " + live.restart_required.join(", "));
    }
    if (live.failed.length) {
      parts.push("failed: " + live.failed.map((f) => f.key).join(", "));
    }
    return parts.join(" — ");
  }

  opts.searchEl.addEventListener("input", applyFilter);
  if (opts.modifiedEl) opts.modifiedEl.addEventListener("change", applyFilter);
  if (opts.nonDefaultEl) opts.nonDefaultEl.addEventListener("change", applyFilter);
  opts.saveEl.addEventListener("click", () => save(false));
  if (opts.liveEl) opts.liveEl.addEventListener("click", () => save(true));
  return load;
}

$$("[data-expand]").forEach((btn) => btn.addEventListener("click", () =>
  $$("#" + btn.dataset.expand + " .grp").forEach((g) => { g.open = true; })));
$$("[data-collapse]").forEach((btn) => btn.addEventListener("click", () =>
  $$("#" + btn.dataset.collapse + " .grp").forEach((g) => { g.open = false; })));

const iniEditor = makeEditor({
  endpoint: "/api/config/ini", fieldsEl: $("#iniFields"), searchEl: $("#iniSearch"),
  dirtyEl: $("#iniDirty"), pathEl: $("#iniPath"), saveEl: $("#iniSave"),
  liveEl: $("#iniSaveLive"),
  navEl: $("#iniNav"), modifiedEl: $("#iniModified"), countEl: $("#iniCount"),
});
LOADERS.ini = async function () {
  await iniEditor();
  await loadAntiCheat();
};
LOADERS.sandbox = makeEditor({
  endpoint: "/api/config/sandbox", fieldsEl: $("#sbFields"), searchEl: $("#sbSearch"),
  dirtyEl: $("#sbDirty"), pathEl: $("#sbPath"), saveEl: $("#sbSave"),
  navEl: $("#sbNav"), modifiedEl: $("#sbModified"), nonDefaultEl: $("#sbNonDefault"),
  countEl: $("#sbCount"),
});

/* ── mods ──────────────────────────────────────────────────── */

let modState = { mods: [], workshop_items: [], map: [], installed: [] };

function linesToList(text) {
  return text.split(/[\n;]+/).map((s) => s.trim()).filter(Boolean);
}

function renderMods() {
  const list = $("#activeMods");
  list.innerHTML = "";
  if (!modState.mods.length) list.innerHTML = "<li><span>no mods active</span></li>";
  modState.mods.forEach((id, index) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    const meta = modState.installed.find((m) => m.mod_id === id);
    span.textContent = meta ? id + "  —  " + meta.name : id;
    li.appendChild(span);
    [["↑", -1], ["↓", 1]].forEach(([glyph, delta]) => {
      const btn = document.createElement("button");
      btn.className = "mv"; btn.textContent = glyph; btn.type = "button";
      btn.addEventListener("click", () => {
        const to = index + delta;
        if (to < 0 || to >= modState.mods.length) return;
        const [item] = modState.mods.splice(index, 1);
        modState.mods.splice(to, 0, item);
        syncModText(); renderMods();
      });
      li.appendChild(btn);
    });
    const rm = document.createElement("button");
    rm.className = "mv"; rm.textContent = "✕"; rm.type = "button";
    rm.addEventListener("click", () => {
      modState.mods.splice(index, 1);
      if (meta) modState.workshop_items = modState.workshop_items.filter((w) => w !== meta.workshop_id);
      syncModText(); renderMods(); renderInstalled();
    });
    li.appendChild(rm);
    list.appendChild(li);
  });
  $("#modsDirty").textContent = "unsaved changes pending";
}

function syncModText() {
  $("#modsRaw").value = modState.mods.join("\n");
  $("#workshopRaw").value = modState.workshop_items.join("\n");
  $("#mapRaw").value = modState.map.join("\n");
}

function renderInstalled() {
  const needle = $("#modSearch").value.trim().toLowerCase();
  const host = $("#installedMods");
  host.innerHTML = "";
  const items = modState.installed.filter((m) =>
    !needle || (m.name + " " + m.mod_id + " " + m.workshop_id).toLowerCase().includes(needle));
  if (!items.length) {
    host.textContent = modState.installed.length
      ? "no match"
      : "no Workshop mods found — the server downloads them on first start";
    return;
  }
  items.forEach((mod) => {
    const active = modState.mods.includes(mod.mod_id);
    const card = document.createElement("div");
    card.className = "mod" + (active ? " active" : "");
    const btn = document.createElement("button");
    btn.className = "btn tiny " + (active ? "ghost" : "go");
    btn.textContent = active ? "remove" : "add";
    btn.addEventListener("click", () => {
      if (active) {
        modState.mods = modState.mods.filter((m) => m !== mod.mod_id);
        modState.workshop_items = modState.workshop_items.filter((w) => w !== mod.workshop_id);
      } else {
        modState.mods.push(mod.mod_id);
        if (!modState.workshop_items.includes(mod.workshop_id)) {
          modState.workshop_items.push(mod.workshop_id);
        }
      }
      syncModText(); renderMods(); renderInstalled();
    });
    const title = document.createElement("b");
    title.textContent = mod.name;
    const meta = document.createElement("small");
    meta.textContent = "id " + mod.mod_id + "  ·  workshop " + mod.workshop_id
      + (mod.maps.length ? "  ·  maps: " + mod.maps.join(", ") : "");
    card.append(btn, title, meta);
    host.appendChild(card);
  });
}

LOADERS.mods = async function () {
  refreshOfflineUpdates();
  const data = await api("/api/config/mods");
  if (!data.ok) return toast(data.error || "load failed", true);
  modState = data;
  $("#workshopPath").textContent = data.workshop_root;
  syncModText();
  renderMods(); renderInstalled();
  $("#modsDirty").textContent = "";
};

["modsRaw", "workshopRaw", "mapRaw"].forEach((id) => $("#" + id).addEventListener("input", () => {
  modState.mods = linesToList($("#modsRaw").value);
  modState.workshop_items = linesToList($("#workshopRaw").value);
  modState.map = linesToList($("#mapRaw").value);
  renderMods(); renderInstalled();
}));
$("#modSearch").addEventListener("input", renderInstalled);
$("#modsSave").addEventListener("click", async () => {
  const res = await api("/api/config/mods", {
    body: { mods: modState.mods, workshop_items: modState.workshop_items, map: modState.map },
  });
  if (!res.ok) return toast(res.error || "save failed", true);
  toast("mod list saved — restart the server to apply");
  LOADERS.mods();
});

/* ── launcher config ───────────────────────────────────────── */

let cfgData = {};

function dig(obj, dotted) {
  return dotted.split(".").reduce((node, part) => (node == null ? node : node[part]), obj);
}
function plant(obj, dotted, value) {
  const parts = dotted.split(".");
  let node = obj;
  parts.slice(0, -1).forEach((part) => { node = node[part] = node[part] || {}; });
  node[parts[parts.length - 1]] = value;
}

function jobRow(job, kind) {
  const row = document.createElement("div");
  row.className = "job";
  const enabled = document.createElement("input");
  enabled.type = "checkbox"; enabled.checked = job.enabled !== false; enabled.title = "enabled";
  const time = document.createElement("input");
  time.type = "time"; time.value = job.time || "05:00";
  row.append(enabled, time);
  if (kind === "restart") {
    const warns = document.createElement("input");
    warns.className = "warns";
    warns.placeholder = "warn minutes, e.g. 15,5,1";
    warns.value = (job.warn_minutes || []).join(",");
    row.appendChild(warns);
  }
  const rm = document.createElement("button");
  rm.className = "rm"; rm.textContent = "✕"; rm.type = "button";
  rm.addEventListener("click", () => { row.remove(); markCfgDirty(); });
  row.appendChild(rm);
  row.querySelectorAll("input").forEach((i) => i.addEventListener("input", markCfgDirty));
  return row;
}

function readJobs(hostId, kind) {
  return Array.from($("#" + hostId).children).map((row) => {
    const inputs = row.querySelectorAll("input");
    const job = { enabled: inputs[0].checked, time: inputs[1].value };
    if (kind === "restart") {
      job.warn_minutes = (inputs[2].value || "").split(",")
        .map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n) && n > 0);
    }
    return job;
  }).filter((job) => job.time);
}

function markCfgDirty() { $("#cfgDirty").textContent = "unsaved changes"; }

LOADERS.launcher = async function () {
  loadProfiles();
  const data = await api("/api/config/launcher");
  if (!data.ok) return toast(data.error || "load failed", true);
  cfgData = data.config;
  $$("[data-cfg]").forEach((el) => {
    const value = dig(cfgData, el.dataset.cfg);
    if (el.type === "checkbox") el.checked = Boolean(value);
    else if (Array.isArray(value)) el.value = value.join("\n");
    else el.value = value == null ? "" : String(value);
  });
  $("#cmdline").textContent = (data.cmdline || []).join(" \n  ");
  const restarts = $("#restartJobs"); restarts.innerHTML = "";
  (dig(cfgData, "schedule.restarts") || []).forEach((j) => restarts.appendChild(jobRow(j, "restart")));
  const backups = $("#backupJobs"); backups.innerHTML = "";
  (dig(cfgData, "schedule.backups") || []).forEach((j) => backups.appendChild(jobRow(j, "backup")));
  $("#cfgDirty").textContent = "";
};

$$("[data-cfg]").forEach((el) => el.addEventListener("input", markCfgDirty));
$("#addRestart").addEventListener("click", () => {
  $("#restartJobs").appendChild(jobRow({ time: "05:00", warn_minutes: [15, 5, 1] }, "restart"));
  markCfgDirty();
});
$("#addBackup").addEventListener("click", () => {
  $("#backupJobs").appendChild(jobRow({ time: "04:30" }, "backup"));
  markCfgDirty();
});

$("#cfgSave").addEventListener("click", async () => {
  const patch = {};
  $$("[data-cfg]").forEach((el) => {
    const key = el.dataset.cfg;
    const current = dig(cfgData, key);
    let value;
    if (el.type === "checkbox") value = el.checked;
    else if (Array.isArray(current)) value = el.value.split("\n").map((s) => s.trim()).filter(Boolean);
    else if (el.type === "number") value = el.value === "" ? 0 : Number(el.value);
    else value = el.value;
    plant(patch, key, value);
  });
  plant(patch, "schedule.restarts", readJobs("restartJobs", "restart"));
  plant(patch, "schedule.backups", readJobs("backupJobs", "backup"));
  const res = await api("/api/config/launcher", { body: { config: patch } });
  if (!res.ok) return toast(res.error || "save failed", true);
  toast("launcher settings saved");
  LOADERS.launcher();
});

$("#rconTest").addEventListener("click", async () => {
  const res = await api("/api/rcon/test", { method: "POST", body: {} });
  toast(res.ok ? "RCON ok: " + (res.message || "").split("\n")[0] : "RCON: " + res.error, !res.ok);
});

/* ── backups ───────────────────────────────────────────────── */

LOADERS.backups = async function () {
  loadBackupAudit();
  const data = await api("/api/backups");
  if (!data.ok) return toast(data.error || "load failed", true);
  $("#backupDir").textContent = data.dir;
  const rows = $("#backupRows");
  rows.innerHTML = "";
  if (!data.backups.length) {
    rows.innerHTML = "<tr><td colspan='3'>no backups yet</td></tr>";
    return;
  }
  data.backups.forEach((b) => {
    const tr = document.createElement("tr");
    [b.name, b.size_mb + " MB", new Date(b.mtime * 1000).toLocaleString()].forEach((text) => {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    });
    const actions = document.createElement("td");
    const restore = document.createElement("button");
    restore.className = "btn ghost tiny";
    restore.textContent = "restore";
    restore.addEventListener("click", () => restoreBackup(b.name, restore));
    actions.appendChild(restore);
    tr.appendChild(actions);
    rows.appendChild(tr);
  });
};

// Restore is destructive and cannot be undone from the panel, so it asks twice:
// once to show what is in the archive, once to type the world name.
async function restoreBackup(name, button) {
  const info = await api("/api/backups/inspect?name=" + encodeURIComponent(name));
  if (!info.ok) return toast(info.error || "cannot read that archive", true);

  const world = info.world;
  const configs = info.config_files.length
    ? "\nConfig files: " + info.config_files.join(", ")
    : "\nNo config files in this archive.";
  const summary =
    "Restore " + info.name + "?\n\n" +
    "This REPLACES the current world (" + world + ") with " +
    info.save_files + " files from " + info.size_mb + " MB archive." +
    configs +
    "\n\nThe server must be stopped. Your current world is backed up first.";
  if (!window.confirm(summary)) return;

  const typed = window.prompt('Type the world name "' + world + '" to confirm:');
  if (typed !== world) return toast("restore cancelled", true);

  button.disabled = true;
  button.textContent = "restoring...";
  const res = await api("/api/backups/restore", {
    method: "POST",
    body: { name: name, confirm: true },
  });
  button.disabled = false;
  button.textContent = "restore";

  if (!res.ok) return toast(res.error || "restore failed", true);
  toast(
    "restored " + res.restored + " - previous world kept as " + res.displaced,
    false
  );
  LOADERS.backups();
}

$("#backupNow").addEventListener("click", async (ev) => {
  ev.target.disabled = true;
  ev.target.textContent = "Backing up...";
  const res = await api("/api/backups/run", { method: "POST", body: {} });
  ev.target.disabled = false;
  ev.target.textContent = "Back up now";
  toast(res.ok ? "backup " + res.name + " (" + res.size_mb + " MB)" : res.error, !res.ok);
  LOADERS.backups();
});

/* ── moderation ────────────────────────────────────────────── */

function modButton(action, name) {
  const btn = document.createElement("button");
  btn.className = "btn tiny " + (action === "ban" ? "danger" : "ghost");
  btn.textContent = action;
  btn.addEventListener("click", () => moderate(action, name, btn));
  return btn;
}

async function moderate(action, target, button) {
  // A ban outlives the session and is awkward to reverse, so it is confirmed;
  // a kick is not, since the player can simply rejoin.
  let banIp = false;
  if (action === "ban") {
    if (!window.confirm('Ban "' + target + '" from the server?')) return;
    banIp = window.confirm("Also ban their IP address?\n\nOK = ban IP too, Cancel = account only.");
  }
  const reason = window.prompt("Reason (optional, recorded in the log):", "") || "";

  if (button) button.disabled = true;
  const res = await api("/api/moderate", {
    method: "POST",
    body: { action: action, target: target, reason: reason, ban_ip: banIp },
  });
  if (button) button.disabled = false;

  toast(res.ok ? action + " " + target + " - " + (res.reply || "sent") : res.error || "failed", !res.ok);
  refreshStatus();
}

$("#aclForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const username = $("#aclUser").value.trim();
  const level = $("#aclLevel").value;
  if (!username) return toast("enter a username", true);
  if (level === "admin" && !window.confirm(
      'Give "' + username + '" full admin?\n\nAdmin can use every command, change server ' +
      'settings and manage players.')) return;

  const res = await api("/api/access-level", {
    method: "POST",
    body: { username: username, level: level },
  });
  if (res.ok) $("#aclUser").value = "";
  toast(res.ok ? username + " -> " + level + " — " + (res.reply || "sent") : res.error, !res.ok);
});

$("#modForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const target = $("#modTarget").value.trim();
  if (!target) return toast("enter a name, Steam ID or IP", true);
  await moderate($("#modAction").value, target, null);
  $("#modTarget").value = "";
});

/* ── profiles ──────────────────────────────────────────────── */

async function loadProfiles() {
  const data = await api("/api/profiles");
  const list = $("#profileList");
  const copy = $("#profileCopy");
  list.innerHTML = "";
  copy.innerHTML = '<option value="">start fresh</option>';
  if (!data.ok) return;

  data.profiles.forEach((p) => {
    const li = document.createElement("li");
    li.className = "playerrow";

    const label = document.createElement("span");
    label.textContent = p.name + (p.current ? " (in use)" : "");
    if (!p.has_config) {
      const note = document.createElement("small");
      note.textContent = "no config yet";
      label.appendChild(note);
    } else if (!p.has_save) {
      const note = document.createElement("small");
      note.textContent = "no world yet";
      label.appendChild(note);
    }
    li.appendChild(label);

    if (!p.current) {
      const btn = document.createElement("button");
      btn.className = "btn tiny ghost";
      btn.textContent = "use";
      btn.addEventListener("click", () => switchProfile(p.name, p.has_config));
      li.appendChild(btn);
    }
    list.appendChild(li);

    if (p.has_config) {
      const opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = "copy " + p.name;
      copy.appendChild(opt);
    }
  });
}

async function switchProfile(name, hasConfig) {
  const warning = hasConfig
    ? ""
    : "\n\nThis profile has no config. The server will generate a fresh world and default settings on its next start.";
  if (!window.confirm('Switch to profile "' + name + '"?' + warning)) return;

  const res = await api("/api/profiles/switch", { method: "POST", body: { name: name } });
  if (!res.ok) return toast(res.error || "could not switch", true);
  toast("now using " + res.current + (res.note ? " — " + res.note : ""));
  loadProfiles();
  refreshStatus();
}

$("#profileForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const res = await api("/api/profiles/create", {
    method: "POST",
    body: { name: $("#profileName").value, copy_from: $("#profileCopy").value || null },
  });
  if (!res.ok) return toast(res.error || "could not create", true);
  $("#profileName").value = "";
  toast("created " + res.name + " — " + res.note);
  loadProfiles();
});

/* ── notifications ─────────────────────────────────────────── */

$("#notifyTest").addEventListener("click", async () => {
  const res = await api("/api/notify/test", { method: "POST", body: {} });
  toast(res.ok ? "test message sent to Discord" : (res.error || "failed"), !res.ok);
});

/* ── performance ───────────────────────────────────────────── */

async function loadPerf() {
  const data = await api("/api/perf");
  const box = $("#perfResult");
  box.className = "diag on";
  box.innerHTML = "";
  if (!data.ok) { box.textContent = "unavailable"; return; }

  const jvm = data.jvm;
  const head = document.createElement("p");
  head.className = "hint";
  head.textContent =
    "heap " + (jvm.xms || "?") + " to " + (jvm.xmx || "?") +
    (jvm.total_memory_gb ? "  |  machine has " + jvm.total_memory_gb + " GB" : "");
  box.appendChild(head);

  jvm.notes.forEach((n) => {
    const p = document.createElement("p");
    // Errors here stop the server booting; warnings only degrade it.
    p.className = n.level === "error" ? "hint bad" : "hint";
    p.textContent = (n.level === "error" ? "will not start: " : "") + n.message;
    box.appendChild(p);
  });
  if (!jvm.notes.length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = "memory settings look sensible for this machine";
    box.appendChild(p);
  }

  data.ports.ports.forEach((entry) => {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = entry.key + " " + entry.port + ": " + (entry.in_use ? "in use" : "free");
    box.appendChild(p);
  });
  const note = document.createElement("p");
  note.className = "hint";
  note.textContent = data.ports.note;
  box.appendChild(note);

  $("#gcLog").checked = jvm.gc_logging;

  const fields = $("#netOptions");
  fields.innerHTML = "";
  if (data.network.ok) {
    data.network.options.forEach((opt) => {
      const label = document.createElement("label");
      const range = opt.kind === "bool" ? "" : " (" + opt.min + "-" + opt.max + ")";
      label.textContent = opt.key + range;
      const input = document.createElement("input");
      if (opt.kind === "bool") {
        input.type = "checkbox";
        input.checked = String(opt.value).toLowerCase() === "true";
      } else {
        input.type = "number";
        input.min = opt.min;
        input.max = opt.max;
        input.value = opt.value;
      }
      input.dataset.net = opt.key;
      input.dataset.kind = opt.kind;
      label.appendChild(input);
      fields.appendChild(label);
    });
  }
}

$("#perfBtn").addEventListener("click", loadPerf);

$("#gcLog").addEventListener("change", async (ev) => {
  const res = await api("/api/perf/gclog", { method: "POST", body: { enabled: ev.target.checked } });
  toast(res.ok ? "GC logging " + (res.enabled ? "on" : "off") + " - restart to apply" : res.error, !res.ok);
});

$("#netSave").addEventListener("click", async () => {
  const options = {};
  $$("#netOptions [data-net]").forEach((el) => {
    options[el.dataset.net] = el.dataset.kind === "bool" ? el.checked : Number(el.value);
  });
  const res = await api("/api/perf/network", { method: "POST", body: { options: options } });
  toast(res.ok ? "saved " + res.changed.length + " option(s) - restart to apply" : res.error, !res.ok);
});

/* ── server update ─────────────────────────────────────────── */

let suTimer = null;

async function pollServerUpdate() {
  const s = await api("/api/server/update");
  const el = $("#suState");
  if (s.running) {
    el.textContent = "updating... (" + Math.round(s.elapsed_sec || 0) + "s) — see console";
    return false;
  }
  if (s.result) {
    el.textContent = s.result.ok ? "update finished" : (s.result.error || "update failed");
  } else {
    el.textContent = "";
  }
  return true;
}

$("#suRun").addEventListener("click", async () => {
  if (!window.confirm(
    "Update the game server via SteamCMD?\n\n" +
    "The server must be stopped. A backup is taken first.\n" +
    "Your world, config and mods are not touched — only the server files."
  )) return;

  const btn = $("#suRun");
  btn.disabled = true;
  const res = await api("/api/server/update", { method: "POST", body: { confirm: true } });
  if (!res.ok) {
    btn.disabled = false;
    $("#suState").textContent = "";
    return toast(res.error || "could not start the update", true);
  }
  toast("update started — progress in the Dashboard console");
  if (res.safety_backup) toast("backed up as " + res.safety_backup);

  if (suTimer) clearInterval(suTimer);
  suTimer = setInterval(async () => {
    if (await pollServerUpdate()) {
      clearInterval(suTimer);
      suTimer = null;
      btn.disabled = false;
    }
  }, 3000);
});

/* ── config drift ──────────────────────────────────────────── */

$("#driftBtn").addEventListener("click", async () => {
  const box = $("#driftResult");
  box.className = "diag on";
  box.textContent = "asking the server...";
  const data = await api("/api/config/drift");
  box.innerHTML = "";
  if (!data.ok) { box.textContent = data.error || "could not check"; return; }

  if (data.in_sync) {
    box.textContent = "the running server matches this file (" + data.live_count + " options compared)";
    return;
  }
  const head = document.createElement("p");
  head.innerHTML = "<b>" + data.drift.length + " option(s) differ from the running server</b>";
  box.appendChild(head);
  const list = document.createElement("ul");
  list.className = "plain";
  data.drift.forEach((d) => {
    const li = document.createElement("li");
    li.textContent = d.key + " — file: " + (d.file === null ? "(absent)" : d.file) +
      "  |  running: " + d.live;
    list.appendChild(li);
  });
  box.appendChild(list);
  const note = document.createElement("p");
  note.className = "hint";
  note.textContent = "A restart will apply the values in this file, replacing what is running now.";
  box.appendChild(note);
});

/* ── backup audit ──────────────────────────────────────────── */

async function loadBackupAudit() {
  const box = $("#backupAudit");
  const data = await api("/api/backups/audit");
  box.innerHTML = "";
  if (!data.ok) { box.textContent = data.error || ""; return; }

  const summary = document.createElement("p");
  summary.className = "hint";
  summary.textContent =
    "pzctl: " + data.pzctl.archives + " archive(s), keeping " + data.pzctl.retention +
    "  |  game: BackupsOnStart=" + data.game.BackupsOnStart.value +
    ", BackupsPeriod=" + data.game.BackupsPeriod.value +
    ", BackupsCount=" + data.game.BackupsCount.value;
  box.appendChild(summary);

  data.notes.forEach((n) => {
    const p = document.createElement("p");
    p.className = "hint bad";
    p.textContent = n.message;
    box.appendChild(p);
  });
}

/* ── anti-cheat ────────────────────────────────────────────── */

async function loadAntiCheat() {
  const data = await api("/api/anticheat");
  const grid = $("#acGrid");
  grid.innerHTML = "";
  if (!data.ok) {
    $("#acSummary").textContent = data.error || "unavailable";
    return;
  }
  $("#acSummary").textContent = data.enabled_count + " of " + data.total + " enabled";

  data.types.forEach((entry) => {
    const label = document.createElement("label");
    // Only the documented ones are highlighted; the rest stay plain numbers
    // rather than carrying a description nobody has verified.
    if (entry.description) label.className = "noted";

    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = entry.enabled;
    box.dataset.acnum = entry.number;
    label.appendChild(box);

    const text = document.createElement("span");
    const num = document.createElement("span");
    num.className = "acnum";
    num.textContent = "Type " + entry.number;
    text.appendChild(num);
    if (entry.description) {
      const desc = document.createElement("span");
      desc.className = "acdesc";
      desc.textContent = entry.description;
      text.appendChild(desc);
    }
    label.appendChild(text);
    grid.appendChild(label);
  });
}

function setAllAntiCheat(on) {
  $$("#acGrid input[data-acnum]").forEach((box) => (box.checked = on));
}
$("#acAllOn").addEventListener("click", () => setAllAntiCheat(true));
$("#acAllOff").addEventListener("click", () => {
  if (window.confirm("Disable all 24 anti-cheat checks?\n\nThis removes the protection they provide. Normally you only need to turn off the specific check a mod trips."))
    setAllAntiCheat(false);
});

$("#acSave").addEventListener("click", async () => {
  const types = {};
  $$("#acGrid input[data-acnum]").forEach((box) => {
    types[box.dataset.acnum] = box.checked;
  });
  const res = await api("/api/anticheat", { method: "POST", body: { types: types } });
  if (!res.ok) return toast(res.error || "save failed", true);
  toast("anti-cheat saved (" + res.changed.length + " changed) — restart the server to apply");
  loadAntiCheat();
});

/* ── pzctl update check ────────────────────────────────────── */

// Only ever runs when pressed. pzctl makes no other outbound request, and a
// background poll would quietly change that.
$("#updateBtn").addEventListener("click", async () => {
  const btn = $("#updateBtn");
  const link = $("#updateLink");
  btn.disabled = true;
  btn.textContent = "...";
  const res = await api("/api/updates");
  btn.disabled = false;
  btn.textContent = "check";

  if (!res.ok) return toast(res.error || "could not check for updates", true);

  if (res.status === "newer_available") {
    link.textContent = "v" + res.latest + " available";
    link.href = res.url;
    link.classList.add("on");
    toast("pzctl v" + res.latest + " is available (you have v" + res.current + ")");
    offerUpgrade(res);
    return;
  }
  link.classList.remove("on");
  if (res.status === "current") toast("you are on the latest release (v" + res.current + ")");
  else if (res.status === "ahead") toast("you are ahead of the latest release (v" + res.latest + ")");
  else if (res.status === "no_releases") toast("no releases published yet");
  else toast("could not compare versions", true);
});

// Upgrading replaces pzctl's own code and needs a daemon restart to take
// effect, so the confirmation says both things plainly.
async function offerUpgrade(info) {
  const ok = window.confirm(
    "Upgrade pzctl from v" + info.current + " to v" + info.latest + "?\n\n" +
    "The game server must be stopped first.\n" +
    "Your pzctl.json and logs are left untouched, and the current version is kept.\n\n" +
    "After upgrading you must restart pzctl for the new version to run."
  );
  if (!ok) return;

  const res = await api("/api/updates/apply", { method: "POST", body: { confirm: true } });
  if (!res.ok) return toast(res.error || "upgrade failed", true);
  toast("upgraded to v" + res.installed + " - restart pzctl to run it", false);
  window.alert(
    "Upgrade installed.\n\n" + res.note +
    "\n\nPrevious version kept as: " + res.previous_kept_as
  );
}

/* ── mod configuration lint ────────────────────────────────── */

async function runModLint() {
  const box = $("#modLintResult");
  box.className = "diag on";
  box.textContent = "checking...";
  const data = await api("/api/mods/lint");
  box.innerHTML = "";
  if (!data.ok) { box.textContent = data.error || "unavailable"; return; }

  if (!data.problems.length) {
    box.textContent = "no problems found in the mod configuration";
    return;
  }
  const head = document.createElement("p");
  head.className = "hint";
  head.textContent = data.errors + " error(s), " + data.warnings + " warning(s)";
  box.appendChild(head);

  const list = document.createElement("ul");
  list.className = "plain";
  data.problems.forEach((p) => {
    const li = document.createElement("li");
    li.textContent = p.message;
    // Errors stop the server booting; warnings usually will not.
    if (p.level === "error") li.className = "bad";
    list.appendChild(li);
  });
  box.appendChild(list);
}

async function refreshOfflineUpdates() {
  const data = await api("/api/mods/updates");
  const el = $("#modOffline");
  if (!data.ok) { el.textContent = ""; return; }
  if (!data.checked) { el.textContent = data.note || ""; return; }
  el.textContent = data.changed.length
    ? data.changed.length + " mod(s) changed on disk since the last start — restart to pick them up"
    : "nothing changed on disk since the last start";
  el.className = data.changed.length ? "hint bad" : "hint";
}

$("#modLintBtn").addEventListener("click", runModLint);
$("#modSeenBtn").addEventListener("click", async () => {
  const res = await api("/api/mods/seen", { method: "POST", body: {} });
  toast(res.ok ? "baseline recorded for " + res.tracked + " Workshop item(s)" : res.error, !res.ok);
  refreshOfflineUpdates();
});

/* ── mod update check ──────────────────────────────────────── */

let modCheckTimer = null;

function renderModCheck(data) {
  const el = $("#modCheckState");
  if (!data.ok) {
    el.textContent = data.error || "check failed";
    el.className = "hint bad";
    return true;
  }
  if (data.status === "checking") {
    el.textContent = "asking the server... (" + Math.round(data.elapsed_sec || 0) + "s)";
    el.className = "hint";
    return false;
  }
  if (data.status === "update_needed") {
    el.textContent = "mods need updating — restart the server to pick them up";
    el.className = "hint bad";
    return true;
  }
  if (data.status === "no_update_reported") {
    // Deliberately not "up to date": the server has no message for that, so
    // all we know is that it did not complain.
    el.textContent = "no update announced — the server has no 'up to date' reply, so this is the absence of a warning";
    el.className = "hint";
    return true;
  }
  el.textContent = "";
  return true;
}

$("#modCheckBtn").addEventListener("click", async () => {
  const btn = $("#modCheckBtn");
  btn.disabled = true;
  const started = await api("/api/mods/check", { method: "POST", body: {} });
  if (!started.ok) {
    btn.disabled = false;
    return renderModCheck(started);
  }
  renderModCheck({ ok: true, status: "checking", elapsed_sec: 0 });

  if (modCheckTimer) clearInterval(modCheckTimer);
  modCheckTimer = setInterval(async () => {
    const done = renderModCheck(await api("/api/mods/check"));
    if (done) {
      clearInterval(modCheckTimer);
      modCheckTimer = null;
      btn.disabled = false;
    }
  }, 3000);
});

/* ── world controls ────────────────────────────────────────── */

async function gmPost(path, body, label) {
  const res = await api("/api/gm/" + path, { method: "POST", body: body });
  toast(res.ok ? label + " — " + (res.reply || "sent") : res.error, !res.ok);
  return res;
}

$("#tpForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  gmPost("teleport", { who: $("#tpWho").value, destination: $("#tpTo").value }, "teleported");
});

function sendState(on) {
  gmPost("state", {
    state: $("#stateWhich").value,
    username: $("#stateUser").value,
    enabled: on,
  }, $("#stateWhich").value + (on ? " on" : " off"));
}
$("#stateForm").addEventListener("submit", (ev) => { ev.preventDefault(); sendState(true); });
$("#stateOff").addEventListener("click", () => sendState(false));

$$("#weatherBtns .chip").forEach((chip) =>
  chip.addEventListener("click", () => gmPost("weather", { event: chip.dataset.ev }, chip.dataset.ev)));

$("#rainForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const which = $("#rainWhich").value;
  gmPost("weather", { event: which, value: Number($("#rainValue").value) }, which);
});

$("#hordeForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const n = Number($("#hordeCount").value);
  // Zombies cannot be un-spawned, so this one confirms.
  if (!window.confirm("Spawn " + n + " zombies near " + $("#hordeUser").value +
      "?\n\nThis cannot be undone and a large horde will hurt performance.")) return;
  gmPost("horde", { username: $("#hordeUser").value, count: n, confirm: true }, n + " zombies spawned");
});

$("#bcForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const res = await gmPost("broadcast", { message: $("#bcMessage").value }, "broadcast sent");
  if (res.ok) $("#bcMessage").value = "";
});

/* ── spawn vehicle ─────────────────────────────────────────── */

let vehPick = null;
let vehTimer = null;

async function searchVehicles() {
  const term = $("#vehSearch").value.trim();
  const list = $("#vehResults");
  if (!term) {
    list.innerHTML = "";
    $("#vehCount").textContent = "";
    return;
  }
  const data = await api("/api/game/vehicles?limit=10&search=" + encodeURIComponent(term));
  list.innerHTML = "";
  if (!data.ok) {
    $("#vehCount").textContent = data.error || "unavailable";
    return;
  }
  $("#vehCount").textContent = data.total + " match" + (data.total === 1 ? "" : "es");

  data.vehicles.forEach((entry) => {
    const li = document.createElement("li");
    li.className = "playerrow";
    const label = document.createElement("span");
    label.textContent = entry.name;
    const id = document.createElement("small");
    id.textContent = entry.id;
    label.appendChild(id);
    li.appendChild(label);

    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "btn tiny ghost";
    pick.textContent = "select";
    pick.addEventListener("click", () => {
      vehPick = entry.id;
      $("#vehSearch").value = entry.name;
      $("#vehCount").textContent = "selected " + entry.id;
      list.innerHTML = "";
    });
    li.appendChild(pick);
    list.appendChild(li);
  });
}

$("#vehSearch").addEventListener("input", () => {
  vehPick = null;
  clearTimeout(vehTimer);
  vehTimer = setTimeout(searchVehicles, 250);
});

$("#vehForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!vehPick) return toast("pick a vehicle from the search results first", true);
  const res = await api("/api/gm/addvehicle", {
    method: "POST",
    body: { script: vehPick, target: $("#vehTarget").value },
  });
  toast(res.ok ? res.script + " spawned - " + (res.reply || "sent") : res.error, !res.ok);
});

/* ── give item ─────────────────────────────────────────────── */

let itemPick = null;
let itemTimer = null;

// 5000+ items, so search server-side rather than shipping the catalog.
async function searchItems() {
  const term = $("#itemSearch").value.trim();
  const list = $("#itemResults");
  if (!term) {
    list.innerHTML = "";
    $("#itemCount").textContent = "";
    return;
  }
  const data = await api("/api/game/items?limit=12&search=" + encodeURIComponent(term));
  list.innerHTML = "";
  if (!data.ok) {
    $("#itemCount").textContent = data.error || "unavailable";
    return;
  }
  $("#itemCount").textContent =
    data.total + " match" + (data.total === 1 ? "" : "es") +
    (data.total > data.items.length ? " (showing " + data.items.length + ")" : "");

  data.items.forEach((entry) => {
    const li = document.createElement("li");
    li.className = "playerrow";
    const label = document.createElement("span");
    label.textContent = entry.name;
    const id = document.createElement("small");
    // The raw id stays visible, as in the INI and Sandbox editors.
    id.textContent = entry.id;
    label.appendChild(id);
    li.appendChild(label);

    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "btn tiny " + (itemPick === entry.id ? "go" : "ghost");
    pick.textContent = itemPick === entry.id ? "selected" : "select";
    pick.addEventListener("click", () => {
      itemPick = entry.id;
      $("#itemSearch").value = entry.name;
      $("#itemCount").textContent = "selected " + entry.id;
      list.innerHTML = "";
    });
    li.appendChild(pick);
    list.appendChild(li);
  });
}

$("#itemSearch").addEventListener("input", () => {
  itemPick = null;
  clearTimeout(itemTimer);
  itemTimer = setTimeout(searchItems, 250);
});

$("#itemForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (!itemPick) return toast("pick an item from the search results first", true);
  const res = await api("/api/gm/additem", {
    method: "POST",
    body: {
      username: $("#itemUser").value,
      item: itemPick,
      count: Number($("#itemQty").value),
    },
  });
  toast(
    res.ok
      ? res.count + "x " + res.item + " to " + res.username + " — " + (res.reply || "sent")
      : res.error,
    !res.ok
  );
});

/* ── grant XP ──────────────────────────────────────────────── */

// Skills are read from the game install, never hardcoded: the ids differ
// between builds and the display name is not the id.
async function loadPerks() {
  const sel = $("#xpPerk");
  const data = await api("/api/game/perks");
  sel.innerHTML = "";
  if (!data.ok) {
    sel.innerHTML = '<option value="">' + (data.error || "unavailable") + "</option>";
    return;
  }
  data.perks.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id;
    // Show the id too when it differs, so the command sent is never a surprise.
    opt.textContent = p.differs ? p.name + "  (" + p.id + ")" : p.name;
    if (p.description) opt.title = p.description;
    sel.appendChild(opt);
  });
}

$("#xpForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const res = await api("/api/gm/addxp", {
    method: "POST",
    body: {
      username: $("#xpUser").value,
      perk: $("#xpPerk").value,
      xp: Number($("#xpAmount").value),
    },
  });
  toast(
    res.ok
      ? res.xp + " " + res.perk + " XP to " + res.username + " — " + (res.reply || "sent")
      : res.error,
    !res.ok
  );
});

/* ── whitelist ─────────────────────────────────────────────── */

async function refreshWhitelist() {
  const data = await api("/api/whitelist");
  const state = $("#wlState");
  if (!data.ok) {
    state.textContent = data.error || "unavailable";
    $("#wlEnabled").disabled = true;
    return;
  }
  $("#wlEnabled").disabled = false;
  $("#wlEnabled").checked = data.enabled;
  state.textContent = data.enabled
    ? "on — only whitelisted users can join"
    : "off — the server is open to anyone";
}

$("#wlEnabled").addEventListener("change", async (ev) => {
  const enabled = ev.target.checked;
  const res = await api("/api/whitelist/mode", {
    method: "POST",
    body: { enabled: enabled },
  });
  if (!res.ok) {
    toast(res.error || "could not change whitelist mode", true);
    return refreshWhitelist();
  }
  // The file is always written; the live push may still have been refused.
  const live = res.live;
  const suffix = !live
    ? " — restart the server to apply"
    : live.ok
    ? " — applied live"
    : " — saved, but live apply failed: " + (live.error || "see log");
  toast("whitelist " + (enabled ? "enforced" : "disabled") + suffix, live && !live.ok);
  refreshWhitelist();
});

async function whitelistUser(action) {
  const username = $("#wlUser").value.trim();
  if (!username) return toast("enter a username", true);
  const password = $("#wlPass").value;
  if (action === "add" && !password) return toast("a password is required to add a user", true);

  const res = await api("/api/whitelist/user", {
    method: "POST",
    body: { action: action, username: username, password: password },
  });
  if (res.ok) {
    $("#wlUser").value = "";
    $("#wlPass").value = "";
  }
  toast(res.ok ? action + " " + username + " — " + (res.reply || "sent") : res.error, !res.ok);
}

$("#wlForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  whitelistUser("add");
});
$("#wlRemove").addEventListener("click", () => whitelistUser("remove"));

/* ── game logs ─────────────────────────────────────────────── */

let logTimer = null;

LOADERS.logs = async function () {
  const data = await api("/api/logs");
  if (!data.ok) return toast(data.error || "load failed", true);
  $("#logDir").textContent = data.dir;

  const select = $("#logSelect");
  const previous = select.value;
  select.innerHTML = "";

  if (!data.logs.length) {
    const opt = document.createElement("option");
    opt.textContent = "no log files found - has the server run yet?";
    opt.value = "";
    select.appendChild(opt);
    $("#logView").textContent = "";
    $("#logMeta").textContent = "";
    return;
  }

  data.logs.forEach((entry) => {
    const opt = document.createElement("option");
    opt.value = entry.name;
    opt.textContent = entry.name + "  (" + entry.kind + ", " + entry.size_kb + " KB)";
    select.appendChild(opt);
  });
  // Keep the current selection across reloads where possible.
  if (previous && data.logs.some((entry) => entry.name === previous)) select.value = previous;
  await showLog();
};

async function showLog() {
  const name = $("#logSelect").value;
  if (!name) return;
  const view = $("#logView");
  const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 40;

  const data = await api("/api/logs/tail?name=" + encodeURIComponent(name));
  if (!data.ok) {
    $("#logMeta").textContent = "";
    view.textContent = data.error || "could not read that log";
    return;
  }

  view.textContent = data.text || "(empty)";
  $("#logMeta").textContent =
    data.size_kb + " KB total" + (data.truncated ? " - showing the end only" : "");
  // Logs are read tail-first, so the newest lines are at the bottom.
  if (atBottom) view.scrollTop = view.scrollHeight;
}

$("#diagBtn").addEventListener("click", async () => {
  const box = $("#diagResult");
  box.textContent = "reading the logs...";
  box.className = "diag on";
  const data = await api("/api/diagnose");
  box.innerHTML = "";

  if (!data.ok) {
    box.textContent = data.error || "could not read the logs";
    return;
  }

  const head = document.createElement("p");
  head.className = "hint";
  const shown =
    data.error_count > data.errors.length
      ? data.errors.length + " of " + data.error_count + " error blocks"
      : data.error_count + " error block(s)";
  head.textContent = "scanned " + data.scanned.join(", ") + " — showing " + shown;
  box.appendChild(head);

  if (data.suspects.length) {
    const title = document.createElement("p");
    title.innerHTML = "<b>Mods named in the logs</b>";
    box.appendChild(title);
    const list = document.createElement("ul");
    list.className = "plain";
    data.suspects.forEach((s) => {
      const li = document.createElement("li");
      const where = s.in_load_order ? "in your load order" : "not in your current load order";
      li.textContent = s.mod + " — " + s.evidence.join("; ") + " (" + where + ")";
      list.appendChild(li);
    });
    box.appendChild(list);
  } else {
    const none = document.createElement("p");
    none.className = "hint";
    // Saying nothing is better than naming a mod the log never mentioned.
    none.textContent = data.note || "No mod is named in the logs.";
    box.appendChild(none);
  }

  data.errors.forEach((e) => {
    const pre = document.createElement("pre");
    pre.className = "cmdpre";
    pre.textContent = e.log + ":" + e.line + "\n" + e.text;
    box.appendChild(pre);
  });
});

$("#logLevelForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const res = await api("/api/logs/level", {
    method: "POST",
    body: { type: $("#logLevelType").value, level: $("#logLevelValue").value },
  });
  // The server decides whether it knows the category, so report what it said.
  toast(res.ok ? "log level sent — " + (res.reply || "no reply") : res.error, !res.ok);
});

$("#logSelect").addEventListener("change", showLog);
$("#logRefresh").addEventListener("click", showLog);
$("#logFollow").addEventListener("change", (ev) => {
  if (logTimer) clearInterval(logTimer);
  // Only poll while the tab is actually on screen - no point re-reading a log
  // nobody is looking at.
  logTimer = ev.target.checked
    ? setInterval(() => {
        if ($("#tab-logs").classList.contains("on")) showLog();
      }, 5000)
    : null;
});

/* ── boot ──────────────────────────────────────────────────── */

if (!TOKEN) askToken();
loadHistory().then(openStream);
refreshStatus();
refreshWhitelist();
loadPerks();
setInterval(refreshStatus, 3000);
