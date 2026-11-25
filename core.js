/* Pure schedule logic: time zones, weekly -> dated occurrences, search.
   No DOM access, so it can be tested in Node. */
(function (root) {
  "use strict";

  const dtfCache = {};
  function dtf(tz) {
    if (!dtfCache[tz]) {
      dtfCache[tz] = new Intl.DateTimeFormat("en-US", {
        timeZone: tz, hourCycle: "h23",
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
    }
    return dtfCache[tz];
  }

  // Offset (ms) of `tz` from UTC at the instant utcMs.
  function tzOffsetMs(utcMs, tz) {
    const t = Math.floor(utcMs / 1000) * 1000;
    const p = {};
    for (const part of dtf(tz).formatToParts(new Date(t))) p[part.type] = part.value;
    const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
    return asUtc - t;
  }

  // Wall-clock time in `tz` -> UTC milliseconds. `minutes` may exceed 1440.
  function localToUtc(y, m, d, minutes, tz) {
    const naive = Date.UTC(y, m - 1, d, 0, minutes);
    let u = naive - tzOffsetMs(naive, tz);
    const off2 = tzOffsetMs(u, tz);
    u = naive - off2;
    return u;
  }

  // Calendar date (y, m, d) of a UTC instant as seen in `tz`.
  function localDate(utcMs, tz) {
    const p = {};
    for (const part of dtf(tz).formatToParts(new Date(Math.floor(utcMs / 1000) * 1000))) p[part.type] = part.value;
    return { y: +p.year, m: +p.month, d: +p.day };
  }

  function toMin(hhmm) {
    const [h, m] = hhmm.split(":").map(Number);
    return h * 60 + (m || 0);
  }

  function isAuto(show) {
    return !!show.auto || /^auto\s?dj/i.test(show.dj || "");
  }

  // Expand every station's weekly pattern into dated occurrences
  // covering [fromMs, toMs]. Each occurrence has UTC `start`/`end`.
  function buildOccurrences(data, fromMs, toMs) {
    const out = [];
    for (const st of data.stations) {
      if (!st.shows || !st.shows.length) continue;
      const a = localDate(fromMs, st.tz);
      const b = localDate(toMs, st.tz);
      const first = Date.UTC(a.y, a.m - 1, a.d) - 2 * 864e5;
      const last = Date.UTC(b.y, b.m - 1, b.d) + 864e5;
      for (let t = first; t <= last; t += 864e5) {
        const dt = new Date(t);
        const y = dt.getUTCFullYear(), m = dt.getUTCMonth() + 1, d = dt.getUTCDate();
        const dow = dt.getUTCDay();
        for (const sh of st.shows) {
          if (sh.dow !== dow) continue;
          const s = toMin(sh.start);
          let e = toMin(sh.end);
          if (e <= s) e += 1440; // runs past midnight
          const start = localToUtc(y, m, d, s, st.tz);
          const end = localToUtc(y, m, d, e, st.tz);
          if (end < fromMs || start > toMs) continue;
          out.push({
            station: st.id, stationName: st.name,
            show: sh.show, dj: sh.dj, auto: isAuto(sh),
            start, end,
          });
        }
      }
    }
    out.sort((x, y) => x.start - y.start || x.stationName.localeCompare(y.stationName));
    return out;
  }

  function norm(s) {
    return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
  }

  // Every query word must appear in the show title or DJ name.
  function matches(occ, query) {
    const q = norm(query);
    if (!q) return true;
    const hay = norm(occ.show + " " + occ.dj);
    return q.split(" ").every((w) => hay.includes(w));
  }

  const api = { tzOffsetMs, localToUtc, localDate, buildOccurrences, matches, norm, isAuto, toMin };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Core = api;
})(typeof self !== "undefined" ? self : this);
