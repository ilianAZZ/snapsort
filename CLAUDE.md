# CLAUDE.md

Architecture notes for working on Snapchat Memories Sorter. Read this before changing the code.

## What the project is

A local web app that sorts the memories of a Snapchat export Tinder-style: one
memory on screen, one key, the next one. Python backend (standard library only),
HTML/CSS/JS frontend with no build step.

**Hard constraint: zero dependencies.** Do not add `pip install`, npm, or a
compilation step. The project must start with `python3 sorter.py` on a fresh
machine. Python 3.9 is the floor (annotations rely on
`from __future__ import annotations`).

**Language: everything in English** — interface, folder names, code comments,
docs. The project is public.

**Naming.** The product is *Snapchat Memories Sorter*; the package and launcher
are just `sorter`. It used to be called SnapSort, which turned out to be taken by
several photo-organiser apps. Sessions started under that name still resume:
`Session.state_dir()` falls back to a `.snapsort/` folder when `.sorter/` is
absent — keep that shim until it is clearly pointless.

## Invariants never to break

1. **Sources are never modified.** No `os.remove`, no `shutil.move`, no write to
   a source path. "Discard" means "don't copy". The only exception is the
   `mode: "move"` option, reserved for folder sources, which moves into the
   destination — never into the system bin.
2. **Nothing leaves the machine.** The server listens on `127.0.0.1` by default.
   No network calls, no telemetry, no CDN. The OpenStreetMap links are plain
   `<a target="_blank">` anchors the user may click. `--host` exists solely for
   the container, whose network namespace is isolated; never make it the default.
3. **The 22 GB are never extracted in one go.** Any new feature that needs a
   media file goes through `MediaCache.path_for()`.
4. **`_undo_files` only deletes inside the destination.** Any change to that
   function must keep the `commonpath` check against `self.dest`.
5. **The index is written once.** Do not put `items`/`order` back into
   `state.json`: that is 2.6 MB rewritten on every swipe (fixed bug, do not
   regress). See "Persistence" below.
6. **`metadata.py` and `mp4.py` only ever write to an already-copied file.** They
   are called from `_transfer` / `_do_merge` on the destination's `.part` file,
   never on a source. Their functions return `None` when the structure is not
   safe: when in doubt, copy without metadata rather than produce a broken file.

## Structure

```text
sorter.py              launcher (python3 sorter.py)
sorter/
  __main__.py            CLI arguments, autostart mode
  scan.py                source detection, index, metadata matching
  session.py             sorting state, media cache, copy queue
  server.py              HTTP server, JSON API, Range streaming
  metadata.py            writes Exif / QuickTime into the copies
  mp4.py                 ISO-BMFF primitives + lossless segment concatenation
  web/index.html         the 5 screens + the modals (browser, prompt, help, about)
  web/css/               main.css imports base · home · setup · sorter · modal
  web/js/                native ES modules, main.js is the entry point
tools/make_demo.py       generates a fake export (screenshots, UI work)
Dockerfile               published to GHCR on every push to main
docker-compose.yml       the copy-paste way to run the published image
```

**No build, no framework.** The frontend is ES modules loaded straight by the
browser (`<script type="module">`). Do not introduce React, Vite or a bundler:
`python3 sorter.py` must remain enough on a fresh machine. To add behaviour,
create a module under `web/js/` and wire it from `main.js`; for styling, use the
sheet for the area concerned under `web/css/`.

## How the Snapchat data is laid out

An export is several ZIPs of ~2 GB. **Only the first** holds
`json/memories_history.json` and `html/`; all of them hold `memories/`.

Naming: `YYYY-MM-DD_<uuid>-main.{jpg,mp4}` with an optional `-overlay.png` (the
text and drawings added in the app, in a separate file).

`memories_history.json` gives `Date` (UTC), `Media Type` and `Location`
— **but no file name at all**. The link is made through the timestamp: a ZIP
entry's modification date *is* the memory's UTC instant, at the ZIP format's
2-second granularity. That is what `scan._attach_metadata` does (5,879 out of
5,880 on a real export). If you touch that function, check the match rate.

Mind the timezone: `info.date_time` from a ZIP is naive UTC, whereas the `mtime`
of a file extracted by `unzip` is local time. `_scan_zip` and `_scan_dir` handle
those two cases differently — that is deliberate.

## Concepts

**Item** — one memory. `id` = `<date>_<uuid>` (stable, used as the key
everywhere). Carries `container` (the ZIP) + `entry` (the path inside), or just
`entry` for a folder source.

**Decision** — `keep` | `trash` | `fav` | `skip` | `folder` | `merge`. Deciding
again on an already-decided memory first removes the files the previous decision
produced (`_undo_files`), then copies again. `_recount()` recomputes every
folder's counter: call it after any mutation of the decisions.

**Queue** — `Session.queue(start, count)` returns the next memories **not yet
decided** from `start`, each with its absolute index, plus the position to
resume scanning from. That is what guarantees a sorted memory never reappears,
including after an undo or a resumed session. The client keeps a local queue of
8 cards; the top card is `buffer[0]`.

**Copy** — a single thread consumes a queue (`_enqueue` / `_run_worker`). The
interface never waits for a copy. Errors land in `_errors`, are exposed by
`/api/session` and surfaced as toasts; they never block sorting. The copied
file's `mtime` is set to the memory's date.

## Joining split videos

Snapchat caps a recording at ten seconds, so a long video comes back as several
consecutive memories. `merge` appends a clip to the file the previous decision
produced.

- A merged item's decision holds `{"action": "merge", "root": <id>}` and owns no
  file of its own — the file belongs to the root. Group membership is *derived*
  from the decisions (`_members()`), never stored separately, so journal replay
  needs no special case.
- `_do_merge` always rebuilds the whole file from the root plus its members, in
  sort order. It is never appended to. That is what makes undo trivial: drop a
  member, rebuild, and the file comes back one segment shorter.
- `undo()` pops the most recent decision, which is naturally the last segment
  joined. Undoing a root that still has members drops those members too — they
  would have nowhere to go.

`mp4.concat()` copies the samples verbatim and rebuilds the sample tables
(`stts`, `stsc`, `stsz`, `stco`/`co64`, `stss`, `ctts`). It refuses (returns
`None`) when the tracks disagree on codec configuration (`stsd`), timescale or
track count, and when the file is fragmented — a badly glued video is worse than
two separate ones.

Verified on a real export: identical packet counts and sizes, frame-for-frame
identical video decode. Only the audio frames sitting exactly at the joins
differ, because the AAC decoder no longer restarts cold there — which is the
desirable outcome.

## Persistence

Inside `<destination>/.sorter/`:

| File | Contents | Write frequency |
| --- | --- | --- |
| `index.json` | items + order + stats (~3 MB) | once at scan, and if the order changes |
| `state.json` | decisions, folders, cursor, options | batched, ≤ 1×/s, and on shutdown |
| `journal.jsonl` | one line per action | on every action |
| `cache/` | extracted media | LRU eviction (`cache_gb`, 3 GB) |

`save_index()` for the heavy one, `save()` for an immediate state write,
`touch()` for a batched write (use this on hot paths like `decide`).

**No decision is ever lost**, thanks to three mechanisms that complement each
other — if you touch one, keep the others:

1. `journal.jsonl` receives every action immediately.
2. `serve()` traps `SIGTERM`/`SIGHUP` (terminal closed, `kill`) so it goes
   through the `finally` that writes the state.
3. `load()` calls `_recover(saved_at)`, which replays the journal lines newer
   than the written state. That is the net for a `SIGKILL` or a power cut.
   `_journal()` goes quiet during that replay (`_replaying`) so history is not
   duplicated.

Verified: after a `kill -9` following 4 unwritten decisions, all 4 come back.

Deleting `.sorter/` resets the sort without touching the filed files.

## API

Everything is JSON except the media. Errors return `{"error": "…"}` with a 4xx
status.

```text
GET  /api/bootstrap        source suggestions, current session, autostart
GET  /api/browse?path=     server-side file browser
GET  /api/session          full snapshot (stats, counters, scan progress)
GET  /api/queue?start=&count=   next unsorted memories (+ prefetch)
GET  /api/media/{id}/{main|overlay}   binary, supports Range
GET  /api/report           generates and returns REPORT.md
POST /api/session/start    {sources[], dest, options} → starts the scan
POST /api/session/resume   {dest}
POST /api/decide           {id, action, folder?}
POST /api/merge            {id} joins this clip to the previous decision
POST /api/undo             undoes the last decision
POST /api/replay           {action} back into the queue (skipped by default)
POST /api/folders          {name, key?} · /api/folders/update · /api/folders/delete
POST /api/options          {options}
POST /api/reveal           opens a folder in Finder / Explorer
```

## Frontend

One module per responsibility, with no shared global state beyond the exported
objects:

| Module | Role |
| --- | --- |
| `main.js` | wires the modules, fetches the bootstrap, hands over to the router |
| `router.js` · `boot.js` | URL ↔ screen mapping, and the server's current state |
| `dom.js` · `api.js` · `format.js` | cross-cutting helpers (`$`, `escapeHtml`, `api`, `bytes`…) |
| `home.js` | landing page: resume, start a new session, or the About modal |
| `setup.js` | the 3-step wizard |
| `sorter.js` | memory queue, decisions, progress, summary |
| `cards.js` | card building, info panel, drag gesture |
| `folders.js` · `prompt.js` · `browser.js` | folders and modals |
| `sound.js` | audio playback policy |
| `shortcuts.js` | keyboard shortcuts |

Each module exports an `initXxx()` called from `main.js`: the DOM wiring is
explicit and reads in one place. Screens are `<section class="screen">`.

The landing page is two columns on **one** surface — no border, no tint between
them, they are just two columns. Left: mark, title, one sentence. Right: three
`.way` cards (resume / new session / about), each carrying the state that makes
it worth clicking — how far the session got, or which export was found on this
machine. Keep the left short: it was too wordy once already.

**Class names are global here.** `.card` belongs to the memory cards in
`sorter.css`, which loads after `home.css` — reusing it on the landing page gave
the buttons `position: absolute` and the size of a photo card. Prefix or pick a
distinct name; there is no scoping to save you.

The four action colours (`--keep`, `--fav`, `--trash`, `--skip`) fail a
categorical-palette check — outside the lightness band, `--skip` reads as grey,
and `--skip`/`--trash` are only ΔE 6.7 apart under protanopia. They stay as they
are because they are semantic (red discards, green keeps), but that is why they
must never carry meaning alone: always a written label beside the colour, values
in text ink. The same rule applies to any progress or stat display you add — a
meter's track is a dimmer step of its own fill hue, and four headline numbers are
a row of tiles, not a chart.

**Routing.** Every screen owns a URL — `/`, `/new`, `/scan`, `/sort`, `/done` —
so Back, Forward and reload behave. `_send_static` serves `index.html` for any
path with no file extension, and `router.js` maps it to a screen. A route
handler may return *a screen id string* to redirect; anything else, including
the promise an async handler returns, means "stay here". Getting that wrong once
already sent `/sort` to the landing page.

Handlers are also guarded (`if (Sorter.session) return`) so navigating from
inside the app never re-runs the cold-start work. `Boot` holds the server state
and must be refreshed after anything that creates a session, or the guards will
still be looking at the state from page load.

Cards: `Sorter.render()` rebuilds the top 3 cards from `buffer[0..2]`. The last
child of `#cards` is the top card (DOM order does the stacking). Dragging is
Pointer Events in `attachDrag()` (`cards.js`).

**Video sound.** `Sound.start()` tries to play with sound. If the browser
refuses (no interaction on the page yet — the autostart case), it falls back to
muted playback, `Sound.blocked` becomes true, and the first click or keypress
restores it. The <kbd>M</kbd> preference lives in `localStorage`. Do not put
`muted = true` back as a hard default: that was the original bug.

**Join button.** `Sorter.last` remembers what was just decided so the button can
be enabled without asking the server; the server validates anyway. It is cleared
on undo, because the client no longer knows what the previous decision is.

Joining always goes *backwards*: the clip on screen is appended to the video
already filed. Sorting is oldest-first, so that is the natural direction — say
so in any wording you touch ("Join to previous"), because "join" on its own is
ambiguous.

Detection uses `Sorter.topDuration`, the duration the browser measured while
playing the previous clip: a continuation starts within two seconds of
`last.ts + last.duration`. Nothing in the export marks a split recording — the
JSON carries only Date, Media Type and Location — so this timing is the only
signal available, and `auto_join` is off by default because of it.

Styling is split by area (`base` declares the variables, the other three build
on it), unapologetically dark (media look better). The Snapchat yellow accent is
used sparingly. Action colours are semantic: red = discard, green = keep,
yellow = favourite, blue = join.

## Metadata written into files (`metadata.py`)

Written onto the copy at transfer time, when the `embed_metadata` option is on
(the default):

- **JPEG** — a TIFF/Exif block in an `APP1` segment inserted after the JFIF
  `APP0`.
- **PNG** — the same block in an `eXIf` chunk, before the first `IDAT`.
- **MP4** — `mvhd`/`tkhd`/`mdhd` dates rewritten in place, then `©day` and
  `©xyz` in `moov/udta` and the `com.apple.quicktime.*` keys in `moov/meta`
  (the ones Photos reads; entries already present are preserved).

The MP4 trap: growing `moov` shifts `mdat`, and therefore the chunk offsets. Two
cases are handled — a `free` box right after `moov` absorbs the difference and
*nothing* moves (the common case with Snapchat), otherwise
`_shift_chunk_offsets` fixes the `stco`/`co64`. `_looks_sane()` re-reads the
structure before writing.

Date convention: **local** time for capture fields (`DateTimeOriginal`, `©day`,
the Apple key), together with the UTC offset; **UTC** for the fields the spec
defines that way (`mvhd`, `GPSDateStamp`).

## Testing

There is no automated test suite (no dependencies, and you need a real export).
Check by hand against one:

```bash
# scan + metadata
python3 -c "
import sys; sys.path.insert(0,'.')
from sorter.scan import scan
r = scan(['<folder holding the zips>'])
print(r['stats'])   # metadata_matched should be ~= total
"

# end to end
python3 sorter.py --port 8799 --no-browser --dest /tmp/out --source <folder>
curl -s localhost:8799/api/session | python3 -m json.tool
```

`python3 tools/make_demo.py /tmp/demo` builds a fake export when you only need
to work on the interface.

After a change, check: the `metadata_matched` rate, that a decision costs a few
ms (not 68 ms — a sign the index is being rewritten), that the queue never shows
a sorted memory again, that `Range` requests return 206, that produced files
carry their date and GPS, and that nothing is written outside the destination.

To check a metadata or merge change, compare the decoded stream before and
after — it must be identical:

```bash
ffmpeg -v error -i before.mp4 -map 0 -vsync passthrough -f framemd5 -
ffmpeg -v error -i after.mp4  -map 0 -vsync passthrough -f framemd5 -
ffprobe -v error -show_entries format_tags after.mp4
```

Beware two red herrings when reading ffmpeg output: the `non monotonically
increasing dts` warnings are already present in the untouched Snapchat files,
and without `-vsync passthrough` the null muxer drops frames on its own.

For a screenshot without a graphical browser:
`"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" --headless
--screenshot=out.png --window-size=1440,900 --virtual-time-budget=6000
http://127.0.0.1:8799/`

## Style

Code and comments in English, like the interface. Comments should be rare, and
explain *why* (a trap, a non-obvious choice), never *what*. Lines ≤ 100
characters. No debug `print` in shipped code.

## Not done yet

- Burning the overlays into the image (would need Pillow / ffmpeg → breaks the
  "zero dependency" rule; would have to be optional and degradable one day)
- Metadata for fragmented videos (`moof`) and 64-bit `moov` sizes: `embed_mp4`
  deliberately leaves those alone (there are none in a real export)
- Joining videos across a folder source in `move` mode: the segment sources stay
  in place, since only the root is transferred
- Sorting `chat_media/` too (another folder of the export, same approach)
- Duplicate detection (Snapchat bursts produce a lot)
- A grid view for a quick overview before sorting
