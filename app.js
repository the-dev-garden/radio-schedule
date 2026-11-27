(function () {
  "use strict";
  const C = window.Core;
  const UK = "Europe/London";
  const DAY_MS = 864e5;

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- storage (wrapped: can throw in private windows) ----------
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
  };

  const state = {
    data: null,
    view: "now",
    stations: new Set(store.get("stations", [])), // empty = all
    dayOffset: 0,
    hideAuto: store.get("hideAuto", true),
    favOnly: false,
    favs: new Set(store.get("favs", [])),
    q: "",
    playing: null,
    pstatus: "",
  };

  // ---------- UK time formatting ----------
  const fTime = new Intl.DateTimeFormat("en-GB", { timeZone: UK, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const fDay = new Intl.DateTimeFormat("en-GB", { timeZone: UK, weekday: "short", day: "numeric", month: "short" });
  const fWk = new Intl.DateTimeFormat("en-GB", { timeZone: UK, weekday: "short" });
  const fDn = new Intl.DateTimeFormat("en-GB", { timeZone: UK, day: "numeric", month: "short" });
  const fLong = new Intl.DateTimeFormat("en-GB", { timeZone: UK, weekday: "long", day: "numeric", month: "long" });
  const t = (ms) => fTime.format(ms);
  const range = (o) => t(o.start) + "–" + t(o.end);
  const dayKey = (ms) => { const d = C.localDate(ms, UK); return d.y * 10000 + d.m * 100 + d.d; };

  // ---------- data ----------
  let occ = [];
  function rebuild() {
    const now = Date.now();
    occ = C.buildOccurrences(state.data, now - DAY_MS, now + 9 * DAY_MS);
  }
  const visible = (o) => state.stations.size === 0 || state.stations.has(o.station);
  const isFav = (dj) => state.favs.has(C.norm(dj));

  // ---------- header chips ----------
  function renderChips() {
    const box = $("stationChips");
    const allOn = state.stations.size === 0;
    let h = `<button class="chip" data-st="" aria-pressed="${allOn}">All stations</button>`;
    for (const s of state.data.stations) {
      h += `<button class="chip" data-st="${esc(s.id)}" aria-pressed="${state.stations.has(s.id)}">${esc(s.name)}</button>`;
    }
    box.innerHTML = h;
  }

  // ---------- On now ----------
  function renderNow() {
    const now = Date.now();
    let html = "";
    for (const st of state.data.stations) {
      if (state.stations.size && !state.stations.has(st.id)) continue;
      const mine = occ.filter((o) => o.station === st.id);
      const cur = mine.filter((o) => o.start <= now && o.end > now).sort((a, b) => a.auto - b.auto || b.start - a.start);
      const curIds = new Set(cur);
      const next = mine.filter((o) => o.start > now && !curIds.has(o)).slice(0, 3);

      html += `<article class="card"><h2>${cur.length ? '<span class="live" title="On air"></span>' : ""}${esc(st.name)}${playBtn(st)}<a href="${esc(st.site || st.url)}" target="_blank" rel="noopener">station site &#8599;</a></h2>`;
      if (!st.shows || !st.shows.length) {
        html += `<div class="meta">No schedule loaded for this station yet.</div>`;
      } else if (!cur.length) {
        html += `<div class="meta">Nothing listed for this hour.</div>`;
      } else {
        const main = cur[0];
        const pct = Math.max(0, Math.min(100, ((now - main.start) / (main.end - main.start)) * 100));
        html += `<div class="${main.auto ? "auto" : ""}"><div class="now-show">${esc(main.show)}</div>
          <div class="now-dj">${esc(main.dj)}${main.auto ? '<span class="tag">auto</span>' : ""}</div>
          <div class="meta">${range(main)} &middot; ends in ${untilText(main.end - now)}</div>
          <div class="bar" aria-hidden="true"><i style="width:${pct.toFixed(0)}%"></i></div></div>`;
        for (const o of cur.slice(1)) {
          html += `<div class="meta">Also listed: <b>${esc(o.show)}</b> &middot; ${esc(o.dj)} (${range(o)})</div>`;
        }
      }
      if (next.length) {
        html += `<div class="next">` + next.map((o) =>
          `<div class="${o.auto ? "auto" : ""}"><time>${t(o.start)}</time><span><b>${esc(o.show)}</b><br><span class="meta">${esc(o.dj)}${startsDay(o.start)}</span></span></div>`).join("") + `</div>`;
      }
      if (st.note) html += `<div class="note">${esc(st.note)}</div>`;
      html += `</article>`;
    }
    $("nowGrid").innerHTML = html || `<div class="empty">No stations selected.</div>`;
  }
  function startsDay(ms) {
    return dayKey(ms) === dayKey(Date.now()) ? "" : " &middot; " + fWk.format(ms);
  }
  function untilText(ms) {
    const m = Math.max(1, Math.round(ms / 60000));
    if (m < 60) return m + " min";
    const h = Math.floor(m / 60), r = m % 60;
    return h + "h" + (r ? " " + r + "m" : "");
  }

  // ---------- Day ----------
  function renderDayPicker() {
    const base = Date.now();
    let h = "";
    for (let i = 0; i < 7; i++) {
      const ms = base + i * DAY_MS;
      h += `<button class="day" data-off="${i}" aria-pressed="${state.dayOffset === i}"><b>${i === 0 ? "Today" : fWk.format(ms)}</b>${fDn.format(ms)}</button>`;
    }
    $("dayPicker").innerHTML = h;
    $("hideAuto").setAttribute("aria-pressed", String(state.hideAuto));
  }
  function slotHtml(o, opts) {
    const now = Date.now();
    const cls = ["slot"];
    if (o.start <= now && o.end > now) cls.push("cur");
    else if (o.end <= now) cls.push("past");
    if (o.auto) cls.push("auto");
    const star = (o.auto || !o.dj) ? "" :
      `<button class="star" data-dj="${esc(o.dj)}" aria-pressed="${isFav(o.dj)}" aria-label="Follow ${esc(o.dj)}" title="Follow this DJ">${isFav(o.dj) ? "&#9733;" : "&#9734;"}</button>`;
    return `<div class="${cls.join(" ")}"><time>${range(o)}</time><div class="who"><div class="t">${esc(o.show)}</div><div class="d">${esc(o.dj)}${opts && opts.station ? (o.dj ? ' <span class="meta">&middot; ' : '<span class="meta">') + esc(o.stationName) + "</span>" : ""}</div></div>${star}</div>`;
  }
  function renderDay() {
    renderDayPicker();
    const target = dayKey(Date.now() + state.dayOffset * DAY_MS);
    let html = "";
    for (const st of state.data.stations) {
      if (state.stations.size && !state.stations.has(st.id)) continue;
      const list = occ.filter((o) => o.station === st.id && dayKey(o.start) === target && !(state.hideAuto && o.auto));
      html += `<section class="card"><h2>${esc(st.name)}${playBtn(st)}</h2>`;
      if (!st.shows || !st.shows.length) html += `<div class="meta">No schedule loaded for this station yet.</div>`;
      else if (!list.length) html += `<div class="meta">Nothing listed${state.hideAuto ? " (Auto DJ hidden)" : ""}.</div>`;
      else html += list.map((o) => slotHtml(o)).join("");
      html += `</section>`;
    }
    $("dayCols").innerHTML = html || `<div class="empty">No stations selected.</div>`;
  }

  // ---------- Find ----------
  function renderFind() {
    $("favOnly").setAttribute("aria-pressed", String(state.favOnly));
    $("hideAuto2").setAttribute("aria-pressed", String(state.hideAuto));
    const box = $("results");
    const q = state.q.trim();
    if (!q && !state.favOnly) {
      box.innerHTML = `<div class="empty">Type a DJ or show name to see when they are next on air in the coming week.<br>Tap &#9734; beside a DJ to follow them, then use <b>Favourites only</b>.</div>`;
      return;
    }
    if (state.favOnly && !state.favs.size) {
      box.innerHTML = `<div class="empty">No favourites yet. Tap &#9734; beside a DJ in the Day or Find views.</div>`;
      return;
    }
    const now = Date.now();
    const end = now + 7 * DAY_MS;
    const res = occ.filter((o) => o.end > now && o.start < end && visible(o) &&
      !(state.hideAuto && o.auto) && (!state.favOnly || isFav(o.dj)) && C.matches(o, q));
    if (!res.length) { box.innerHTML = `<div class="empty">No shows found in the next 7 days.</div>`; return; }
    const shown = res.slice(0, 200);
    let html = `<div class="res">`, lastKey = null;
    for (const o of shown) {
      const k = dayKey(o.start);
      if (k !== lastKey) { html += `<h3>${k === dayKey(now) ? "Today" : fLong.format(o.start)}</h3>`; lastKey = k; }
      html += slotHtml(o, { station: true });
    }
    html += `</div>`;
    if (res.length > shown.length) html += `<div class="empty">Showing the first ${shown.length} of ${res.length}. Narrow your search.</div>`;
    box.innerHTML = html;
  }

  // ---------- footer ----------
  function renderFoot() {
    const gen = state.data.generated ? new Date(state.data.generated) : null;
    const when = gen && !isNaN(gen) ? fDay.format(gen) + " " + t(gen.getTime()) : "unknown";
    const seed = state.data.source === "seed" ? " This is the starter copy; run the scraper to refresh it." : "";
    $("foot").innerHTML = `All times are UK time (GMT/BST), converted from each station's own time zone. Schedules last updated ${esc(when)}.${esc(seed)} ` +
      `Shows can change at short notice, so check the station's own page too.`;
  }

  // ---------- live audio ----------
  const audio = new Audio();
  audio.preload = "none";
  let tryList = [], tryIdx = 0;
  const fresh = (u) => u.replace(/([?&](?:t|nocache)=)\d+/, "$1" + Date.now()); // cache-busters

  function stationById(id) { return state.data.stations.find((s) => s.id === id); }
  function playBtn(st) {
    if (!st.streams || !st.streams.length) return "";
    const on = state.playing === st.id;
    return ` <button class="play" data-play="${esc(st.id)}" aria-pressed="${on}" aria-label="${on ? "Stop" : "Play"} ${esc(st.name)}" title="${on ? "Stop" : "Listen live"}">${on ? "&#9632;" : "&#9654;"}</button>`;
  }
  function loadStream() {
    audio.src = fresh(tryList[tryIdx]);
    const p = audio.play();
    if (p && p.catch) p.catch(() => {}); // failures arrive via the "error" event
  }
  function startPlay(id) {
    const st = stationById(id);
    if (!st || !st.streams || !st.streams.length) return;
    state.playing = id; state.pstatus = "Connecting…";
    tryList = st.streams; tryIdx = 0;
    loadStream(); render();
  }
  function stopPlay() {
    state.playing = null;
    audio.pause(); audio.removeAttribute("src"); audio.load();
    render();
  }
  function togglePlay(id) { state.playing === id ? stopPlay() : startPlay(id); }

  audio.addEventListener("error", () => {
    if (!state.playing) return;
    if (tryIdx + 1 < tryList.length) { tryIdx++; loadStream(); return; }
    state.pstatus = "fail"; renderPlayer();
  });
  audio.addEventListener("playing", () => { if (state.playing) { state.pstatus = "Playing live"; renderPlayer(); } });
  audio.addEventListener("waiting", () => { if (state.playing) { state.pstatus = "Buffering…"; renderPlayer(); } });

  function renderPlayer() {
    const box = $("player");
    if (!box) return;
    if (!state.playing || !state.data) {
      box.hidden = true; document.body.classList.remove("has-player"); return;
    }
    const st = stationById(state.playing);
    const now = Date.now();
    const live = occ.filter((o) => o.station === st.id && o.start <= now && o.end > now).sort((a, b) => a.auto - b.auto || b.start - a.start)[0];
    const onAir = live ? live.show + (live.dj && !live.auto ? " · " + live.dj : "") : "";
    let status;
    if (state.pstatus === "fail") {
      const direct = fresh(tryList[tryList.length - 1]);
      status = `Couldn't play this stream here. <a href="${esc(direct)}" target="_blank" rel="noopener">Open it directly &#8599;</a>`;
    } else {
      status = (state.pstatus === "Playing live" ? '<span class="eq">&#9679;</span> ' : "") + esc(state.pstatus || "") + (onAir ? " &middot; " + esc(onAir) : "");
    }
    box.innerHTML = `<button class="play" id="stopBtn" aria-pressed="true" aria-label="Stop" title="Stop">&#9632;</button><div class="pinfo"><b>${esc(st.name)}</b><span class="meta">${status}</span></div>`;
    box.hidden = false; document.body.classList.add("has-player");
    if ("mediaSession" in navigator && window.MediaMetadata) {
      try {
        navigator.mediaSession.metadata = new MediaMetadata({ title: live ? live.show : "Live", artist: st.name });
        navigator.mediaSession.setActionHandler("pause", stopPlay);
        navigator.mediaSession.setActionHandler("stop", stopPlay);
      } catch (e) { /* not supported */ }
    }
  }

  // ---------- render ----------
  function render() {
    if (!state.data) return;
    $("clock").textContent = fDay.format(Date.now()) + " · " + t(Date.now()) + " UK";
    if (state.view === "now") renderNow();
    else if (state.view === "day") renderDay();
    else renderFind();
    renderPlayer();
  }
  function setView(v) {
    state.view = v;
    document.querySelectorAll(".view").forEach((el) => el.classList.toggle("active", el.id === "view-" + v));
    document.querySelectorAll("nav.tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
    render();
  }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const pb = e.target.closest("[data-play]");
    if (pb) return togglePlay(pb.dataset.play);
    if (e.target.closest("#stopBtn")) return stopPlay();
    const tab = e.target.closest("nav.tabs button");
    if (tab) return setView(tab.dataset.view);

    const chip = e.target.closest("#stationChips .chip");
    if (chip) {
      const id = chip.dataset.st;
      if (!id) state.stations.clear();
      else if (state.stations.has(id)) state.stations.delete(id);
      else state.stations.add(id);
      store.set("stations", [...state.stations]);
      renderChips(); return render();
    }
    const day = e.target.closest(".day");
    if (day) { state.dayOffset = +day.dataset.off; return render(); }

    const star = e.target.closest(".star");
    if (star) {
      const k = C.norm(star.dataset.dj);
      if (state.favs.has(k)) state.favs.delete(k); else state.favs.add(k);
      store.set("favs", [...state.favs]);
      return render();
    }
    if (e.target.closest("#hideAuto") || e.target.closest("#hideAuto2")) {
      state.hideAuto = !state.hideAuto; store.set("hideAuto", state.hideAuto); return render();
    }
    if (e.target.closest("#favOnly")) { state.favOnly = !state.favOnly; return render(); }
  });
  $("q").addEventListener("input", (e) => { state.q = e.target.value; renderFind(); });

  function sizeHeader() { document.documentElement.style.setProperty("--hh", $("hdr").offsetHeight + "px"); }
  window.addEventListener("resize", sizeHeader);

  // ---------- boot ----------
  async function load() {
    let data = null;
    try {
      const r = await fetch("schedule.json", { cache: "no-cache" });
      if (r.ok) data = await r.json();
    } catch (e) { /* offline or opened as a local file */ }
    if (!data) data = window.SCHEDULE_SEED;
    return data;
  }

  load().then((data) => {
    state.data = data;
    // Stations in alphabetical order (ignoring punctuation and case), whatever order the data arrives in.
    data.stations.sort((a, b) => a.name.localeCompare(b.name, "en-GB", { sensitivity: "base", ignorePunctuation: true }));
    renderChips(); renderFoot(); rebuild(); sizeHeader(); render();
    setInterval(() => { rebuild(); render(); }, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { rebuild(); render(); } });
  });

  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
})();
