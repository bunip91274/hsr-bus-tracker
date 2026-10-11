const REFRESH_MS = 30_000;
const $ = (id) => document.getElementById(id);
const select = $("residence");

// ---------- tiny storage helpers (localStorage can throw in private mode) ----------
const store = {
  get(key, fallback = null) {
    try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
  },
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function badge(route, color, textColor) {
  const b = el("span", "badge", route);
  b.style.background = color;
  b.style.color = textColor;
  return b;
}

function updatedText(data, okText) {
  const t = new Date(data.generated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return data.realtime.ok ? `${okText} ${t}` : `${okText} ${t} · live data unavailable, using schedule`;
}

// ====================================================================================
// Plan a trip
// ====================================================================================
const destInput = $("dest");
const suggestions = $("suggestions");
const arriveBy = $("arrive-by");
let destination = store.get("destination"); // {stop_id, name}
let searchTimer = null;

function setDestination(d) {
  destination = d;
  destInput.value = d.name;
  store.set("destination", d);
  const recents = [d, ...store.get("recents", []).filter((r) => r.stop_id !== d.stop_id)].slice(0, 4);
  store.set("recents", recents);
  renderRecents();
  hideSuggestions();
  loadPlan();
}

function renderRecents() {
  const box = $("recents");
  box.replaceChildren();
  for (const r of store.get("recents", [])) {
    if (destination && r.stop_id === destination.stop_id) continue;
    const b = el("button", "", r.name);
    b.type = "button";
    b.addEventListener("click", () => setDestination(r));
    box.append(b);
  }
}

function hideSuggestions() {
  suggestions.hidden = true;
  suggestions.replaceChildren();
}

async function searchStops(q) {
  if (q.trim().length < 2) return hideSuggestions();
  const res = await fetch(`/api/stops/search?q=${encodeURIComponent(q)}`);
  const hits = await res.json();
  suggestions.replaceChildren();
  if (!hits.length) {
    suggestions.append(el("li", "none", "No stop by that name on a direct bus from your res."));
  }
  for (const h of hits) {
    const li = el("li", "", h.name);
    li.setAttribute("role", "option");
    // pointerdown fires before the input loses focus, so the tap isn't swallowed
    li.addEventListener("pointerdown", (e) => { e.preventDefault(); setDestination(h); });
    suggestions.append(li);
  }
  suggestions.hidden = false;
}

destInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchStops(destInput.value), 150);
});
destInput.addEventListener("focus", () => destInput.select());
destInput.addEventListener("blur", () => {
  setTimeout(hideSuggestions, 100);
  if (destination) destInput.value = destination.name; // undo half-typed text
});
destInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const first = suggestions.querySelector("li[role=option]");
    if (first) first.dispatchEvent(new Event("pointerdown"));
  }
  if (e.key === "Escape") destInput.blur();
});

for (const radio of document.querySelectorAll("input[name=when]")) {
  radio.addEventListener("change", () => {
    arriveBy.disabled = radio.value === "now" && radio.checked;
    if (!arriveBy.disabled && !arriveBy.value) arriveBy.value = "09:00";
    loadPlan();
  });
}
arriveBy.addEventListener("change", loadPlan);

function optionCard(o) {
  const card = $("option-tpl").content.cloneNode(true);
  card.querySelector(".leave-time").textContent = o.leave_by;
  const inEl = card.querySelector(".leave-in");
  inEl.textContent = o.leave_in_min === 0 ? "Go now!" : `in ${o.leave_in_min} min`;
  if (o.leave_in_min <= 5) inEl.classList.add("soon");

  card.querySelector(".badge").replaceWith(badge(o.route, o.color, o.text_color));
  card.querySelector(".headsign").textContent = o.headsign;
  const live = card.querySelector(".live-tag");
  live.textContent = o.live ? "Live" : "Scheduled";
  if (o.live) live.classList.add("on");

  card.querySelector(".walk-in").textContent = `Walk ${o.walk_to_min} min to ${o.board_stop}`;
  const bus = card.querySelector(".bus");
  bus.append(`Bus leaves ${o.depart} `, el("span", "muted", `· ${o.num_stops} stop${o.num_stops === 1 ? "" : "s"} · ${o.ride_min} min`));
  const walkOut = card.querySelector(".walk-out");
  if (o.walk_from_min > 0) walkOut.textContent = `Get off at ${o.alight_stop}, walk ${o.walk_from_min} min`;
  else walkOut.textContent = `Get off at ${o.alight_stop}`;
  card.querySelector(".arrive").textContent = `Arrive ${o.arrive}`;
  return card;
}

async function loadPlan() {
  const status = $("plan-status");
  const box = $("options");
  if (!destination || !select.value) {
    box.replaceChildren();
    status.textContent = destination ? "" : "Pick a destination to see when to leave.";
    return;
  }
  const params = new URLSearchParams({ res: select.value, to: destination.stop_id });
  const byMode = document.querySelector("input[name=when]:checked").value === "by";
  if (byMode && arriveBy.value) params.set("arrive_by", arriveBy.value);

  try {
    const res = await fetch(`/api/plan?${params}`);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    box.replaceChildren();
    if (data.message) box.append(el("p", "message", data.message));
    data.options.forEach((o) => box.append(optionCard(o)));
    status.textContent = updatedText(data, "Updated");
  } catch {
    status.textContent = "Couldn't reach the server. Retrying…";
  }
}

// ====================================================================================
// Next buses board
// ====================================================================================
function depRow(d) {
  const li = el("li", "dep" + (d.canceled ? " canceled" : ""));
  const when = el("span", "when");
  const mins = el("span", "mins", d.canceled ? "—" : d.minutes === 0 ? "Now" : `${d.minutes} min`);
  let sub;
  if (d.canceled) sub = el("span", "sub late", "Cancelled");
  else if (d.live && d.delay_min >= 2) sub = el("span", "sub late", `${d.delay_min} min late · ${d.time}`);
  else if (d.live) sub = el("span", "sub live", `Live · ${d.time}`);
  else sub = el("span", "sub", `Scheduled · ${d.time}`);
  when.append(mins, sub);
  li.append(badge(d.route, d.color, d.text_color), el("span", "headsign", d.headsign), when);
  return li;
}

async function loadBoard() {
  if (!select.value) return;
  try {
    const res = await fetch(`/api/residences/${encodeURIComponent(select.value)}`);
    if (!res.ok) throw new Error(res.status);
    const data = await res.json();
    const box = $("stops");
    box.replaceChildren();
    if (!data.stops.length) box.append(el("p", "empty", "No stops found nearby. Check this residence's coordinates."));
    for (const s of data.stops) {
      const node = $("stop-tpl").content.cloneNode(true);
      node.querySelector(".stop-name").textContent = s.name;
      node.querySelector(".walk").textContent = `${s.walk_min} min walk`;
      const ul = node.querySelector(".deps");
      if (!s.departures.length) ul.append(el("li", "empty", "No buses in the next 90 min."));
      s.departures.forEach((d) => ul.append(depRow(d)));
      box.append(node);
    }
    $("status").textContent = updatedText(data, "Updated");
  } catch {
    $("status").textContent = "Couldn't reach the server. Retrying…";
  }
}

// ====================================================================================
// Tabs, polling, startup
// ====================================================================================
let tab = store.get("tab", "plan");
let timer = null;

function refresh() {
  return tab === "plan" ? loadPlan() : loadBoard();
}

function showTab(name) {
  tab = name;
  store.set("tab", name);
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  $("plan").hidden = name !== "plan";
  $("board").hidden = name !== "board";
  clearInterval(timer);
  refresh();
  timer = setInterval(refresh, REFRESH_MS);
}

async function init() {
  const residences = await (await fetch("/api/residences")).json();
  for (const r of residences) select.add(new Option(r.name, r.id));
  const last = store.get("residence");
  if (last && residences.some((r) => r.id === last)) select.value = last;
  select.addEventListener("change", () => { store.set("residence", select.value); refresh(); });

  for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
  // Refresh as soon as you come back to the app instead of showing stale times.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  if (destination) destInput.value = destination.name;
  renderRecents();
  showTab(tab);
}

init();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => { /* app still works without it */ });
}
