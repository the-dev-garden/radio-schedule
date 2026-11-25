# Rock 'n' Roll Radio Guide

Shows and DJs for Rockabilly Radio, Rockin 247 Radio, SplinterWood Radio and
Rockin' Rhythm & Blues, all in UK time. Works on phone and PC.

* **On now** – what's playing on each station, with what's next
* **Day** – the full running order for any of the next 7 days
* **Find** – search a DJ or show and see when they're next on; tap ☆ to follow DJs

## Try it right now
Double-click `index.html`. (The install-to-phone feature needs it hosted, see below.)

## Put it online (free, ~5 minutes)
1. Create a GitHub account, then a new **public** repository.
2. Upload every file in this folder (including the hidden `.github` folder).
3. Repository **Settings → Pages → Deploy from branch → main / (root)**.
4. After a minute you get a link like `https://yourname.github.io/yourrepo/`.
5. **Phone:** open the link, then *Add to Home screen* (iPhone: Share → Add to Home Screen).
   **PC:** open the link; in Chrome/Edge use the install icon in the address bar.

## Keeping schedules fresh
`.github/workflows/update.yml` runs `scraper/scrape.py` every day and saves the
result. To run it immediately: repository → **Actions → Update schedules → Run workflow**.
Run it once straight after uploading, because the bundled data is a starter copy.

If one station breaks (they redesign their site), it keeps its last good data and
the others still update. The Actions log says which one failed.

## Known gaps – please read
* **Rockin 247 Radio is empty until the scraper has run.** Its schedule is a widget
  hosted on radio.co and I could not inspect it. If the scraper can't read it,
  copy the shows from the station's printable PDF into `manual/rockin247.json`
  (times in US Pacific, `dow` 0=Sunday…6=Saturday; see the other stations in
  `schedule.json` for the format) and it will be used instead.
  Check which time zone that widget uses and change `tz` in `scraper/scrape.py` if needed.
* **Scrapers were tested against sample HTML, not the live sites** (this environment
  couldn't reach them). The first live run is the real test.
* Rockin' Rhythm & Blues is shown in US Eastern on its source page; the bundled copy
  came from a stale snapshot, so refresh it before trusting it.
* Rockabilly Radio's page includes "X or Auto DJ" and "alternate weeks" entries; they're shown as written.
* "Follow" favourites are stored in your own browser only, per device.

## Files
`index.html app.js core.js` the app · `schedule.json/.js` the data ·
`scraper/` the updater · `sw.js manifest.webmanifest icons/` install + offline
