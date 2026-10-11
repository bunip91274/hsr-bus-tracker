const REFRESH_MS = 30_000;
const select = document.getElementById("residence");
const stopsEl = document.getElementById("stops");
const statusEl = document.getElementById("status");
const tpl = document.getElementById("stop-tpl");
let timer = null;

function saved() {
  try { return localStorage.getItem("residence"); } catch { return null; }
}
function save(id) {
  try { localStorage.setItem("residence", id); } catch { /* private mode etc. */ }
}

function depRow(d) {
  const li = document.createElement("li");
  li.className = "dep" + (d.canceled ? " canceled" : "");

  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = d.route;
  badge.style.background = d.color;
  badge.style.color = d.text_color;

  const head = document.createElement("span");
  head.className = "headsign";
  head.textContent = d.headsign;

  const when = document.createElement("span");
  when.className = "when";
  const mins = document.createElement("span");
  mins.className = "mins";
  mins.textContent = d.canceled ? "—" : d.minutes === 0 ? "Now" : `${d.minutes} min`;
  const sub = document.createElement("span");
  if (d.canceled) {
    sub.className = "sub late";
    sub.textContent = "Cancelled";
  } else if (d.live && d.delay_min >= 2) {
    sub.className = "sub late";
    sub.textContent = `${d.delay_min} min late · ${d.time}`;
  } else if (d.live) {
    sub.className = "sub live";
    sub.textContent = `Live · ${d.time}`;
  } else {
    sub.className = "sub";
    sub.textContent = `Scheduled · ${d.time}`;
  }
  when.append(mins, sub);

  li.append(badge, head, when);
  return li;
}

function render(data) {
  stopsEl.replaceChildren();
  if (!data.stops.length) {
    stopsEl.innerHTML = '<p class="empty">No stops found nearby. Check this residence\'s coordinates.</p>';
  }
  for (const s of data.stops) {
    const node = tpl.content.cloneNode(true);
    node.querySelector(".stop-name").textContent = s.name;
    node.querySelector(".walk").textContent = `${s.walk_min} min walk`;
    const ul = node.querySelector(".deps");
    if (!s.departures.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No buses in the next 90 min.";
      ul.append(li);
    }
    s.departures.forEach((d) => ul.append(depRow(d)));
    stopsEl.append(node);
  }
  const t = new Date(data.generated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  statusEl.textContent = data.realtime.ok
    ? `Updated ${t}`
    : `Updated ${t} · live data unavailable, showing schedule`;
}

async function load() {
  const id = select.value;
  if (!id) return;
  try {
    const res = await fetch(`/api/residences/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error(res.status);
    render(await res.json());
  } catch (e) {
    statusEl.textContent = "Couldn't reach the server. Retrying…";
  }
}

function startPolling() {
  clearInterval(timer);
  load();
  timer = setInterval(load, REFRESH_MS);
}

async function init() {
  const residences = await (await fetch("/api/residences")).json();
  for (const r of residences) select.add(new Option(r.name, r.id));
  const last = saved();
  if (last && residences.some((r) => r.id === last)) select.value = last;
  select.addEventListener("change", () => { save(select.value); startPolling(); });
  // Refresh immediately when you come back to the tab instead of showing stale times.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  startPolling();
}

init();
