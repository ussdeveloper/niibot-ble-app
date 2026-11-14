# Changelog

All notable changes to **Niibot Studio** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

## [0.15.3] — 2026-11-14

### Changed
- **The panel makes no external requests.** It loaded Roboto from Google Fonts on every start,
  which contradicts a local-first printer tool and slowed the first paint offline; the stylesheet
  now uses the system UI font and the webfont links are gone.
- **One version number.** `pyproject.toml` takes the version from `niibot/version.py` instead of
  carrying its own stale copy, and `niibot.__version__` re-exports the same value.
- Documentation review: the start-up reconnection chapter describes the per-printer workers
  (discovery, retry after 60 s / 20 s, name matching), the old “Layers” section is folded into
  *Items*, and the About panel no longer mixes a Polish sentence into the English UI.

### Fixed
- The repository contained no user data, but the tests, one docstring and a screenshot caption
  still used the author's own Bluetooth addresses and printer serials; they are now synthetic
  (`C9:48:8A:00:00:01`, `B1-G000000001`), and the serial and MAC visible in the screenshots are
  masked. A note under *Screenshots* says that all company and product data there is fictional.

## [0.15.2] — 2026-11-14

### Fixed
- **Typing into a text element could fail with `Input should be a valid integer`.** A freshly added
  element was built with only a handful of fields, so the inspector showed empty *Font* and
  *Rotation*, and once a number field was involved the spec went out with `NaN` — which JSON turns
  into `null` — and the server refused the whole label, so every keystroke bounced. Elements are
  now always created from one complete set of defaults (`makeElement`), the spec is sanitised on
  its way to the server and to the draft, and a cleared number field no longer writes `NaN`.
- The server treats `null` in an element as "use the default" instead of rejecting the label, and
  clamps line spacing, font size and thickness, so an already-saved draft with nulls previews and
  prints again.

## [0.15.1] — 2026-11-14

### Added
- **Turn the editing view a quarter to the right** — the ⟳ button next to the zoom chip (or
  *View → Orientation*). A tall sticker such as a 10 × 20 mm cell can then be typed on lying down;
  the label, its calibration template and the printed bitmap stay exactly as they are, because the
  turn is only how the canvas is drawn. Click again for 180°/270°, double-click to go back to 0°;
  the angle is remembered per browser profile.
- Picking, dragging, resizing and `Ctrl`+wheel zoom all work in the turned view: the pointer is
  rotated back into label millimetres, and the resize grip keeps following the element's own
  bottom-right corner instead of a screen corner.

### Changed
- *View → Orientation* now separates the two things it used to mix: turning the **view** (no shape
  change) and swapping the label's own width/height (which really changes the print page and warns
  when the roll already has a calibration).

## [0.15.0] — 2026-11-14

### Changed
- **One worker per printer.** Every printer now has its own task with its own driver, lock, timer,
  ping and reconnect schedule (`PrinterWorker`), instead of one ping loop that visited all links in
  turn. A slow Fichero status read or a BLE connect attempt can no longer delay the keep-alive of
  the B1 next to it — which is what used to drop a perfectly healthy link. The printing lock is
  the active printer's lock, so a job and a ping on the same printer still never interleave.
- **The protocol belongs to the printer, not to the app.** Each printer remembers the family that
  answered (`printers.protocol`), and the global *Protocol* switch is only the default for devices
  the app has never talked to. Older builds stored the protocol of the last connection globally,
  which made the next printer start with the wrong family; that value is cleared once on start.
- A printer that comes back under a **new BLE address** is recognised by its advertised name (it
  carries the serial): the stale row is merged away and its worker stopped, so one physical printer
  can never hold two BLE links — the collision that kept both links flapping.
- Discovery only reports who is advertising; the workers decide when to connect, so a printer that
  is switched off is waited for instead of being hit with a connect timeout every cycle. A name two
  different units share (the random tail of `D11s_xxxx_BLE`) never matches by name, so the app
  cannot connect the wrong unit.

### Fixed
- When an attempt fails with the wrong family, the worker tries the other one and **remembers the
  one that answered** — and it does not waste that second attempt on a printer that is not even
  advertising (`DeviceNotFound`).
- If the active printer drops while another link is still up, the app prints on the surviving
  printer instead of reporting "not connected".

## [0.14.13] — 2026-11-14

### Added
- **Bluetooth status inside the Settings menu.** The top-bar *Settings* dropdown now starts with a
  live block: a green dot and the model plus protocol of the active printer, its link name and BLE
  address, the print head (dpi / px) and battery level, then one line per extra connected printer —
  or, when nothing is linked, the remembered printer and the auto-connect state. The menu also gained
  **Choose printer…**, which opens the same switcher as the status bar without rescanning.

### Fixed
- **Dragging an element no longer dims its neighbours.** The whole preview used to drop to 28 %
  opacity while an element travelled, which also faded everything underneath it. The lifted element
  now leaves a hatched patch exactly where it was picked up, the rest of the label keeps full
  contrast, and the ghost rides above the mirror-region tints so the moving artwork stays crisp.

## [0.14.12] — 2026-11-14

### Added
- **Zoom and pan on the work area.** `Ctrl`+wheel zooms around the cursor (the label point under the
  pointer stays put), the chip in the bottom-right corner steps through the levels with a *fit*
  button, `Ctrl+=` / `Ctrl+-` / `Ctrl+0` do the same from the keyboard. Pan with the middle button,
  `Space`+drag or by dragging the empty canvas. The zoom level is stored in the database.
- The element palette moved to the **top-right corner** of the work area (it used to float above the
  label) and its grip drags it anywhere; the position is remembered, double-clicking the grip puts
  it back in the corner.

### Fixed
- **Selecting stacked elements follows what they paint.** Hit-testing used the whole bounding box, so
  a large text box with `auto_fit` (or a QR in a wide box, or an image with *contain*) swallowed
  clicks aimed at the artwork underneath even where it drew nothing. A text element now answers only
  over its glyph block, a QR only over its square matrix, an image only over the fitted picture, and
  an unfilled frame only on its border — with a small tolerance so thin items stay easy to grab.

## [0.14.11] — 2026-11-14

### Added
- **Gap calibration for the Fichero/Karsten family.** These printers cannot report a label size,
  but they do have a gap sensor: with paper type *gap* every form feed walks the paper until the
  sensor sees the gap. **Settings → Paper → Calibrate gap** (`POST /api/printer/gap-feed`) now
  forces exactly that: it sets the paper type, sends two form feeds (`10 0C`, which answers `OK`,
  falling back to `1D 0C`) and reports for each step whether the printer answered and still sees
  paper. The B1 and friends refuse with a pointer to *Detect format*, because they report their
  geometry themselves.
- **Measuring strip** (`GET /api/calibration-strip`, `niibot/calibration.py`): a label with a
  millimetre scale along the paper direction, numbers every 5 mm. Printing it on the real roll and
  reading the value at the fold gives the label height the D11s cannot report; *Save size for this
  roll* stores it per printer and roll.

## [0.14.10] — 2026-11-14

### Fixed
- **Backspace no longer deletes the selected element.** It is the key everyone presses to fix a
  typo, and it removed the whole element when the focus was not in a text field. Deleting now has
  exactly three paths: the **Delete** key, the right-click menu and the trash button in the panel
  (the calibrator's zone selection follows the same rule).

### Added
- **Right-click menu on an element**: Duplicate, Move up, Move down, Hide/Show and Delete, with the
  element selected by the click that opened the menu.

### Changed
- The panel section called *Layers* is now **Items** (`Elementy`, `Elemente`, `Éléments`, …), which
  is what it actually lists; the reorder buttons say *Move up* / *Move down*.

## [0.14.9] — 2026-11-14

### Fixed
- **A printer that was already connected no longer drops while another one is being connected.**
  Connecting held the print lock for up to 25 s, which stopped the keep-alive pings and let the
  linked printer hit its supervision timeout (observed: the B1 dropped every time the Fichero was
  tried). Connection attempts now use their own lock and the pings run in a separate loop, so a
  slow scan or connect cannot starve them — measured `ping_age_s` 3–4 s during a Fichero attempt.
- **Connection errors are readable.** Instead of `Could not connect: ` (a bare `TimeoutError` has no
  message) the panel now says what happened: *not advertising — switched off, asleep, or connected
  to another device*, *no answer — switched off, out of range, or holding another connection*, or
  *dropped the link right after connecting*. The reason shows in the right-click printer menu next
  to the printer that failed.
- The Fichero family is retried sooner (20 s) than other printers (60 s) after a failed attempt,
  because these units re-advertise only for short windows.

## [0.14.8] — 2026-11-14

### Fixed
- A background connection attempt no longer takes the printer away from the app: the new link is
  prepared off to the side, so the panel keeps printing on the active printer while another one is
  being connected (before, the UI briefly reported "not connected" for the whole attempt).
- The first printer that answers becomes the active one immediately, instead of waiting for every
  other known printer to be tried first.

### Removed
- The single-printer retry/alternative helpers left over from the previous design.

## [0.14.7] — 2026-11-14

### Added
- **Several printers connected at the same time.** A background manager keeps a link to every
  printer the app knows while that printer is advertising, pings each of them (so none idles out)
  and drops the ones that were switched off. Everything the app printed before is still per
  printer — switching just changes which library and which link is active.
- **Instant printer switching.** `POST /api/activate` swaps the active link with no Bluetooth work
  (measured: 225 ms including a status refresh, 3 ms for the refusal when the printer is away), and
  the right-click menu on the connection chip no longer scans — it opens at once and marks every
  printer as *printing now*, *connected*, *in range* or *known, not in range*. Switching shows a
  toast while it happens and one when it is done.
- **Connect all printers in range** in the same menu, and the status bar lists every open link in
  its tooltip with the active one marked.
- `GET /api/connections` and a `connections` array in `GET /api/state`.

### Changed
- **Design is the default view.** A restored draft never reopens in the sheet; the switch in the
  Properties header is one click away.

## [0.14.6] — 2026-11-14

### Added
- The printer-queue section has its own **resizer**: drag the handle above it to give the queue as
  much room as you like, double-click it to go back to the default third of the panel. The height
  is remembered in SQLite with the other panel sizes.

### Fixed
- **The app now connects by itself when the printer is there.** A scan that sees the remembered
  printer connects immediately instead of waiting for the timer, the retry interval is 8 s for the
  first attempts and 18 s after that, and the status bar narrates it.
- Windows keeps advertising a printer from cache after it was switched off, so the remembered
  printer could look reachable while it was asleep. An advertisement without a name (that is what
  the cache returns) now gets a single connect attempt instead of three, a failed address is
  skipped for 90 s, and…
- …when the remembered printer does not answer, the app connects to **another known printer that
  is advertising right now** (it has its own library already) and remembers it as last used,
  instead of sitting dead while a printer is in range.
- Auto-connect now does the same book-keeping as a manual connect (`reconnect_wanted`, device
  scope, claiming the legacy library), so the keep-alive watchdog keeps the link alive after it.

## [0.14.5] — 2026-11-14

### Fixed
- **The app did not reconnect to the remembered printer after it was switched on.** The
  reachability probe used `BleakScanner.find_device_by_address`, which on Windows hands back a
  cached, nameless `BLEDevice` for an address that is *not* advertising (and raises on other
  attempts) — so a printer that was off either looked reachable or blocked the attempt, and each
  cycle wasted a 25 s connect timeout. The probe is now the same advertisement scan the printer
  list uses, a connect is retried three times with a fresh scan, and the retry interval starts at
  10 s before backing off to 25 s.
- A printer with a **random BLE address** comes back under a new address after a power cycle: the
  server now remembers its advertised name too, matches by name (only when exactly one printer
  matches, never guessing between two identical units) and adopts the new address.
- The keep-alive loop no longer overwrites the auto-connect status, so the status bar keeps saying
  what the reconnect is actually doing ("looking for D11s…", "not in range", "connect attempt 1/3
  failed (TimeoutError)").

### Added
- **Printer queue** at the bottom of the right panel: every job with its state, size, time and
  printer, the running job on top with a progress bar. Each row has **Repeat** (prints the exact
  stored label or sheet again) and **Remove** (drops it from the history); a running job offers
  **Cancel** instead. Backed by `GET /api/queue`, `POST /api/queue/{id}/repeat` and
  `DELETE /api/queue/{id}`, with the spec stored in the `print_jobs` history so a repeat needs no
  open label.
- **Ready / Not Ready chip** in the top-right corner: it reports whether the local backend answers
  (`GET /api/health`, polled every 3 s, tooltip with version, uptime, latency and printer state).
  The Bluetooth printer state stays in the status bar, where it belongs.
- The **Design / Sheet switch moved into the Properties panel header** — the floating chip over the
  canvas could not be moved out of the way.

## [0.14.4] — 2026-11-14

### Fixed
- **QR codes and barcodes print with uneven modules.** Both were drawn from a fractional module
  width once the box was narrow, so bars landed on half dots and a phone could not read the result.
  Modules are now always a whole number of dots, the matrix is centred, and a **quiet zone** is
  reserved whenever the box has room for it — that white margin is what scanners lock onto.
- A QR code smaller than its own module count used to be drawn clipped (a corner of the matrix
  simply fell outside the box). It is now either drawn whole or refused with an explicit warning
  instead of printing an unreadable label.

### Added
- **The editor blocks shrinking a code below what the printer can print.** `niibot/codes.py` counts
  the modules of the QR payload (or the barcode pattern) and converts them to millimetres at the
  connected printer's dpi: one dot per module is the hard floor the resize handle stops at, two dots
  per module (0.25 mm at 203 dpi, 0.17 mm at 300 dpi) is the size scanners are happy with. The
  inspector shows both numbers with the module count, an amber frame marks a code that is printable
  but risky, and **Fit** grows it to the safe size.
- `POST /api/code-size` exposes the same numbers to the UI for any payload/format without rendering.
- QR codes now stay square while resizing — a stretched matrix does not scan.

## [0.14.3] — 2026-11-14

### Changed
- **The printer list only shows printers.** A device is listed when it is known (connected at least
  once → it owns a library) or when its advertisement looks like a label printer: a Niimbot name
  prefix (`B1`, `D11`, `B21`, …), a LuckPrinter/AiYin name (`FICHERO_`, `D11s_`, `Karsten`, …), the
  Niimbot service UUID or one of the LuckPrinter UART services. Phones, trackers and sensors no
  longer clutter the list.
- New **Show all Bluetooth devices** switch in *Settings → Printer* (remembered in the database)
  brings the full `GET /api/printers?all=true` view back when something has to be diagnosed.
- The list paints immediately from what the server already knows and refreshes after the scan, so
  it is never empty while the radio is busy.

### Fixed
- The off-line status bar and model panel now report the printer that will be used next — name,
  protocol **and** geometry — instead of mixing the saved name with the previous driver's dpi/printhead.

## [0.14.2] — 2026-11-14

### Fixed
- **Fichero/Karsten prints every label but never advances to the next one.** The panel numbers
  label types like Niimbot (1 = labels with gaps) and the value was sent unchanged, so the printer
  switched to *black mark* mode and searched for a mark that does not exist. `paper_type_from_label_type()`
  now maps 1→0 (gap), 2→1 (black mark), 3/5/11→2 (continuous) and the paper type is sent before
  every print, plus once after connecting.
- **Explicit paper advance.** This family does not walk to the tear bar by itself, so the vendor app
  sends the distance: *Settings → Printer → Paper advance* (mm, sent as `1B 4A nn` dots after each
  label) with a **Feed now** button, and `POST /api/printer/feed` for scripted use.
- **The offline status bar shows the printer that will be used next** (name and protocol from the
  printer registry), so the B1 is labelled `B1`/`Niimbot` instead of inheriting the last driver's
  Fichero name. A failed connect restores the saved protocol too, and a successful one stores
  `auto`, so the protocol select stays on *Automatic* instead of pinning the detected family.

## [0.14.1] — 2026-11-14

### Added
- **Keep-alive watchdog with automatic reconnect.** The server pings the printer every 5 s on its
  own (Niimbot heartbeat `0xDC`, LuckPrinter status `10 FF 40`), so an idle BLE link no longer times
  out — even with the panel closed. A dead ping, or a link the printer closed, triggers a silent
  reconnect: the device is rediscovered by the scanner and connected again with the address and
  protocol used last. Measured on a D11s: back on-line in 14–28 s, no clicks.
- `GET /api/state` exposes `keep_alive.ping_age_s` and `keep_alive.reconnects`;
  `POST /api/keepalive/drop` is a test hook that closes the link on purpose.
- **Automatic protocol choice** on connect: the device name decides (Fichero/D11s/Karsten →
  LuckPrinter, otherwise Niimbot) and the other family is tried when the first attempt fails.
  The protocol select is now `Automatic` by default and only there to force a value.
- The printer list is the merged inventory: everything ever connected (**own library**) plus
  everything advertising right now, refreshed by a light background scan when the list opens.

### Fixed
- A disconnect callback from an **old** BLE client no longer marks a freshly re-established link as
  dead (that made recovery bounce), and an explicit Disconnect stops the watchdog so it cannot
  fight the user. A recovery never hijacks a different printer connected meanwhile.

## [0.14.0] — 2026-11-14

### Added
- **One library per printer type.** Labels, templates and roll calibrations are stored per device
  scope (`niimbot:B1`, `luckprinter:FICHERO`, …) in new `device` columns, and the working draft is
  kept per scope in `ui_state` (`draft:<scope>`). Connecting a different printer switches the whole
  workspace: sizes that do not fit its printhead are never offered, and each printer keeps its own
  calibration for the same roll code.
- **Printer registry and switcher.** Every successful connection is remembered in a `printers`
  table (address, protocol, model, scope). The status bar shows `<model> · <protocol> · <device>`
  and a **right-click** opens the list of known printers, so printing can move to another unit
  without a rescan. `GET /api/printers` and `DELETE /api/printers/{address}` back it.
- First run on a new printer type starts from a label clamped to its printhead width (12 mm on a
  96 px D11s, 48 mm on a B1).
- Rows created by earlier builds are claimed by the first Niimbot printer that connects and their
  calibration keys are rewritten to the scoped form.

### Fixed
- Saving a calibration from the UI no longer fails on the missing `device` binding.

## [0.13.13] — 2026-11-14

### Added
- **Fichero / Karsten (AiYin D11s, LuckPrinter SDK) support.** A second, independent driver:
  `niibot/luckprinter.py` (command bytes, `GS v 0` raster, status/info parsing) and
  `niibot/fichero.py` (BLE transport over the UART characteristic pairs plus a printer adapter that
  matches `NiimbotPrinter`). Printing, density, paper type, battery, lid/paper status and the
  Windows queue all work; the sequence is wake → enable → raster → form feed → stop, exactly as
  captured from the vendor app.
- **Protocol selector** in *Settings → Printer* (`Niimbot` / `LuckPrinter (Fichero / AiYin)`),
  stored in the database and applied to auto-connect. Connecting from the device list picks the
  protocol automatically from the advertised name (`FICHERO_…`, `D11s_…`).
- `bitmap.encode_image()` now carries the 1-bit rows, so both protocols print the same render.

### Verified
- Against a real D11s: firmware `2.4.1`, lid closed, paper present, and a 12 × 30 mm test label
  printed (`rows=240, cols=96, state=done`).

## [0.13.12] — 2026-11-14

### Added
- **Device diagnostics** (`niibot/diagnostics.py`, `POST /api/device/diagnose`, `niibot diagnose
  <address>`): connects to any BLE device without the Niimbot framing, dumps the GATT tree, reads
  the readable characteristics and records every notification for a few seconds into a copyable
  report. This is how an unknown label printer (Fichero, Karsten, OEM clones) is identified — the
  command frames show which protocol it speaks.
- The scan result stays visible while a printer is connected, so a second device can be inspected
  without disconnecting the first; each row has a stethoscope button and a type badge.

### Fixed
- **Bluetooth scan no longer hides printers it does not recognise.** The list was filtered to
  Niimbot names/service UUIDs, so any other brand simply never appeared. Every BLE device is listed
  now (known printers first, then by signal strength) and marked *label printer* or *BLE device*;
  the empty-scan message explains the classic-Bluetooth (SPP) case.

## [0.13.11] — 2026-11-14

### Changed
- **Nothing is kept in the browser any more.** The working draft, panel widths, sheet height,
  grid/snap/region toggles, print density, language and the last calibration moved from
  `localStorage` into the SQLite database (new `ui_state` table behind `GET`/`POST /api/ui`).
  A reload, a second window and the packaged EXE now share exactly the same state, and clearing
  the browser cache no longer throws away unsaved work. Failed writes are retried instead of
  being dropped.
- Leftovers from older builds are imported into the database once and then deleted from the
  browser, so a first start loses nothing and afterwards `localStorage` stays empty.

## [0.13.10] — 2026-11-14

### Added
- **Hover picking with a visible target.** Pointing at an element draws a dashed frame with its
  name, so it is obvious what a click will grab before pressing the button.
- **Unfilled frames no longer block what they surround** — an outline rectangle is now caught on
  its border band only, so a text or picture inside a big frame can be clicked and dragged directly.
- **Ctrl+click (Cmd on macOS) selects one element deeper** in the stack, and the hover frame
  previews exactly which element that will be; the chip shows the depth (`1/3`). Previously a large
  element in front made everything behind it unreachable.

## [0.13.9] — 2026-11-14

### Added
- **71 printer models with their real printhead geometry** (`niibot/models.py`): id, resolution and
  printhead width per model, taken from the vendor model list. The B1 stays 203 dpi × 384 px,
  the B1 Pro is 300 dpi × 567 px, B2 Pro 300/567, B21 Pro 300/591, B3S 203/576, B4 Pro
  300/1229, M3 300/851 and so on. Canvas size, label rendering, print packing and the Windows (IPP)
  queue all follow the connected device instead of assuming 203 dpi every time.
- **Printhead override** in `Settings → Printer`: resolution, printhead width and print task can be
  entered by hand and are applied to the live connection immediately. This is the path for printers
  missing from the table (clones/OEM units) — the tab warns when the model is unknown.

### Fixed
- **A resized picture or icon is no longer cropped.** Pictures and icons are inserted with
  `contain` and a stored `cover` is now opt-in, so stretching a box letterboxes the artwork
  instead of cutting it. Icons are also rasterised at the connected printhead width (384 px on a
  B1, 567 px on 300 dpi devices) instead of being upscaled afterwards.
- PDF documents arriving through the Windows print queue are rasterised at the printer's own
  resolution — a 300 dpi device no longer prints an upscaled 203 dpi render.

## [0.13.8] — 2026-11-14

### Fixed
- **A roll swap no longer leaves the previous roll's calibration in the header.** While connected the
  calibration is exactly what the database has for the inserted code; the last-used one is only kept
  on screen when the printer is away (with its warning badge). An uncalibrated roll now shows its
  label size and `Calibrate this roll` instead of another roll's name, zones and „fit” warning.
- A fresh roll with no calibration opens the **calibration library** even when the catalogue knows its
  size, and the stored calibration now also applies its page size automatically.

### Added
- **„Waiting for the roll” spinner**: when the printer reports no paper (lid open, roll out) a toast
  with a spinner stays on screen and the status is polled every 1.5 s, so the new roll and its
  calibration appear the moment the paper is back. The regular status poll went from 25 s to 12 s.

## [0.13.7] — 2026-11-14

### Removed
- **Preview in the calibrator.** The canvas already shows the zones, so a second bitmap of the same
  outlines was noise: the eye button in the title bar and the `Preview` button with its thumbnail are
  gone. `Print zone borders` (title bar or the Print check section) stays — it puts the outline on
  the paper, which is the thing worth checking.

## [0.13.6] — 2026-11-14

### Added
- **The panel opens in its own window.** The executable now starts Edge (or Chrome) in *app mode*
  against `127.0.0.1`: a chromeless window with its own taskbar entry and a private profile in
  `data\window`, instead of a tab in the everyday browser. It waits until the server answers, so the
  window never shows an empty page, and falls back to the default browser when no Chromium is found.
  `--no-browser` keeps the console only.

## [0.13.5] — 2026-11-14

### Changed
- **The first chip in the label bar is the roll itself**: the roll's name in bold when it has one, and
  the label size („42 × 21 mm”) when it does not — instead of the sticker size, which read like a
  contradiction next to a 42 × 21 mm label. The separate name chip is gone.
- The sticker size and print page moved into the chip's tooltip (“sticker 21 × 21 mm · print page
  42 × 21 mm”) and stay visible where they belong: inside the calibrator.

## [0.13.4] — 2026-11-14

### Added
- **Saved calibrations inside the calibrator**: `Start from a saved calibration` loads any stored
  layout (size, grid, zones, name) straight into the window, so a known roll can reuse one without
  drawing anything — then `Save for this roll` stores it for the inserted roll code.
- **Roll identity in the calibrator footer**: roll code (RFID), tag SN and the sticker/page size,
  plus a red *printer off-line* note when disconnected.
- **Preview and print in the calibrator title bar**, next to the close button — the same zone-border
  check print as the `Print check` section.

## [0.13.3] — 2026-11-14

### Added
- **Guide lines in the calibrator**: the label centre and the quarter lines (from the sticker size),
  drawn behind the zones. Dragging or resizing an item snaps its edges and its centre onto them and
  the line lights up (Alt = free movement, no snapping).

### Fixed
- **Distribute H/V** now spreads the selected items across the whole label with equal margins at the
  edges and equal gaps between them; before it only spread them between their own outermost edges,
  and with items wider than that span they landed on top of each other (looking like they vanished).
- Align (left/right/centre/top/bottom) keeps every item on the label.
- The grid (rows × columns) is stored with the calibration, so a calibration reused from the library
  rebuilds its print page correctly instead of collapsing to one sticker.
- The calibrator canvas is sized immediately, so it is never blank when the tab was in the background.

## [0.13.2] — 2026-11-14

### Added
- **Calibration library** (`View → Calibration library…`): every stored calibration as a card with a
  preview of its item zones, the sticker size, the print page and the zone count — with `Use`
  (copies it onto the inserted roll), `Edit` (opens the calibrator with it) and `New calibration`.
- A roll with **no calibration in the database** now asks first: the library opens with the stored
  calibrations, so an existing layout can be reused instead of drawing it again.
- **Off-line badge**: while the printer is disconnected the last calibration stays on screen and a
  warning chip (⚠ *offline calibration*) marks it as not synced with the printer. The calibrate chip
  stays available off-line and edits the remembered roll.

### Changed
- Every connection re-reads the calibration from the database for the inserted roll code and
  re-applies it to the stage.

## [0.13.1] — 2026-11-14

### Fixed
- **The main size in the calibrator is one sticker.** `Sticker width/height` describe a single
  label; the print page is derived from the grid (`2 across × 1 down` = 42 × 21 mm) and shown
  under the fields, with a warning when it would be wider than the printhead.
- **Calibrator resize runs away no longer**: scaling was applied again to already-scaled outlines
  on every pointer move, so an item grew far faster than the cursor. It now scales from a
  snapshot taken when the handle is grabbed, and the edge follows the pointer (no jump).
- Calibrator drag/resize snap to **0.5 mm** by default (Alt = 0.1 mm free) and stay inside the label;
  the drawn grid matches the snap (0.5 mm cells, 5 mm heavier lines).
- A roll without a typed name is now described by its sticker size („21 × 21 mm”) instead of by the
  RFID code — in the label bar, the calibrator and the stored profile.

### Changed
- Design canvas snapping is 0.5 mm too, and its grid draws 0.5 mm cells with heavier 5 mm lines.

## [0.13.0] — 2026-11-14

### Added
- **Item zones instead of cells**: the calibrator was rebuilt around the question it answers —
  where can *one item* be printed. A zone may combine several outlines (rectangle, circle,
  ellipse, square) that belong to the same item; they are merged into a single area and treated
  as one item everywhere (design stage overlay, `Mirror cells`, sheet printing).
- **Right-click menu on a zone**: add another outline to the same item, edit the outlines,
  duplicate the item, copy it onto every other item, delete it.
- **Per-outline editing**: the outline chips in the inspector switch the handles to one outline
  of the item (Alt+click on the label does the same), so a circle can grow a rectangular lobe.
- **Print check**: `Preview` and `Print zone borders` render just the zone borders (and
  optionally the label border) at printer resolution — `/api/roll-format/outline` and
  `/api/roll-format/print`.
- Canvas editor: wheel zoom about the cursor, middle-drag pan, marquee selection, arrow-key
  nudging, square tool, 5 mm guide grid.

### Changed
- The calibrator is a two-column studio (820 px tall) with a scrolling control column and a
  canvas that fills the rest; rows/columns/spacings still rebuild the grid immediately.
- **Lines are gone from the calibrator** — items have width and height, so rectangles,
  squares, circles and ellipses are the only outlines. Legacy `line` regions are dropped.
- A new roll starts with one zone covering the whole label (0.5 mm margin).

## [0.12.10] — 2026-11-13

### Fixed
- **The dragged artwork no longer swells**: the element render is trimmed to its ink, so stretching it
  to `100\u202f% \u00d7 100\u202f%` of the frame blew the glyph up. `/api/element-preview` now returns
  `element_box` (where the element sits inside the returned PNG) and the drag ghost places and scales the
  artwork from it, so what follows the cursor is exactly the size it prints. Also applies while resizing.

## [0.12.9] — 2026-11-13

### Fixed
- **Calibrator handles keep their size**: the resize helpers live inside the zoomed canvas, so at 400 % they
  used to grow to 40 px. They are now counter-scaled and stay 10 px at every zoom level.
- **Image zoom no longer shows 0** for icons and freshly inserted pictures: the field falls back to 1.0
  instead of an empty value, so touching it cannot collapse the image to 5 %.

## [0.12.8] — 2026-11-13

### Added
- **Name for a roll format**: the calibrator has a *Roll name* field, so the label bar and the format
  catalogue show `Round 21 mm white` instead of the bare RFID code. The name is stored with the roll
  (catalogue entry + remembered profile) and comes back on every later insertion.

## [0.12.7] — 2026-11-13

### Added
- **Shape selector in the calibrator**: the selected cell can be switched between
  **Rectangle / Circle / Line** without redrawing it — a circle keeps a square box centred where the cell
  was, a line keeps its length as thickness shrinks to the line weight. Works on a whole multi-selection
  at once.

## [0.12.6] — 2026-11-13

### Added
- **Mirror the calibrated cells**: with a roll that has two or more cells (two circles side by side,
  a 2 × 2 block, …) the label bar offers **Mirror cells N×**. Design inside cell 1 and every other cell
  is filled with the same content — copied, scaled to the cell when its size differs, and optionally
  flipped — both in the live preview and on the printout. Cell 1 is outlined as the editable one while
  this is on, and the toggle warns when elements stick out of it.

## [0.12.5] — 2026-11-13

### Added
- **Label orientation in the main editor**: the *Orientation* chip next to the roll data (and
  `View → Orientation…`) turns the label between landscape and portrait — either
  *Rotate design 90° right / left* (elements travel with the label and their own rotation follows)
  or *Swap the label only* (just exchange width and height and keep the layout). Everything goes through
  the normal undo history.

### Fixed
- The roll × label mismatch chip no longer appears when the label is simply the roll turned by 90°.

## [0.12.4] — 2026-11-13

### Added
- **Multi-selection with Shift+click** in the calibrator (cells or their chips). Dragging any selected cell
  moves the whole group, and the align bar becomes active: **Left / Centre H / Right / Top / Middle V /
  Bottom** plus **Distribute H / Distribute V** for equal spacing inside the selected block.
- Calibrator window is now **760 px wide with a two-column layout** (settings left, canvas and inspector
  right) and a 320 px-tall canvas viewport, with aligned field grids.
- **Row and column spacing** fields next to Rows/Columns: the grid fills the whole label evenly, subtracting
  the spacings (`cellW = (labelW − (cols−1)·spacing) / cols`). Default 0 mm, as before.

### Changed
- Setting **Rows or Columns re-draws the grid instantly** — no separate button press needed.

## [0.12.3] — 2026-11-13

### Added
- **Cell list in the calibrator**: every drawn cell is a chip under the toolbar (`1 rect 48×25 ✕`) — click to
  select, `✕` deletes just that one. The summary now spells out the layout it detected
  (`2 cells · 2 rows × 1 column · 48 × 25 mm each`), and that is what gets stored with the roll.
- **Undo / redo inside the calibrator**: toolbar buttons plus `Ctrl+Z`, `Ctrl+Y` / `Ctrl+Shift+Z`, and
  `Delete` removes the selected cell. The history is local to the dialog, so it never fights the
  designer's own undo.

### Fixed
- Editing the **Rows / Columns** fields now rebuilds the grid immediately while the cells are still a
  plain grid — no second click on the button needed.
- **Lines are visible and selectable**: they render as a dashed violet bar with a minimum size and a
  larger hit area, keep their angle, and their `Length (mm)` / `Angle` / `Thickness (mm)` fields are
  reachable (the dialog grows to fit instead of clipping them).

## [0.12.2] — 2026-11-13

### Added
- **Zoom and pan in the calibrator**: the wheel zooms around the cursor (100–800 %), *Fit* recentres,
  and dragging empty space with the *Select* tool pans the view.
- **Resize handles**: the selected cell gets eight handles (corners and edges) — drag to size it, no
  numbers needed. A line gets two end handles plus a thickness handle, and keeps its angle.
- `Delete` removes the selected cell straight from the keyboard.

### Changed
- Line cells store an `angle` (degrees) and a `thickness_mm`, drawn rotated on both the calibrator and
  the label; rectangles and circles use `thickness_mm` for their outline weight.

## [0.12.1] — 2026-11-13

### Added
- **Draw the cells with the mouse**: the calibrator has a tool row — *Select*, *Draw rectangle*,
  *Draw circle*, *Draw line*. Pick a tool and drag on the label to draw the printable area exactly
  where it sits (a circle is snapped to a round box, a line to a thin rule). A plain click drops the
  shape at its default size, and the tool stays active so several cells can be drawn in a row.
- Cells can be lines as well (`shape: "line"`), drawn over the label and honoured by the API.

## [0.12.0] — 2026-11-13

### Added
- **Roll calibrator**: an unknown RFID code now always opens the calibrator instead of a size prompt.
  Set width/height/gap, then draw the printable cells — **Grid from rows × columns** for a regular
  roll, or **Add rectangle / Add circle** and drag them where the data goes (two circles side by side,
  four cells in a 2 × 2 block, anything). Everything is stored per roll code, so the next insertion
  configures itself.
- **Correct calibration** button in the label bar next to the roll chips — reopen the calibrator at any
  time, also for rolls that are already known.
- **Drawn cells are visible while designing** (dashed outlines with numbers over the label) and a
  **roll × label mismatch** chip offers to fit the label to the roll in one click.
- The catalogue import also reads the **new Flutter NIIMBOT app** (`niimbot_db.isar`), which stores roll
  records under the same eight-digit `oneCode` the RFID tag reports. Six vendor formats now resolve
  automatically, and grid rolls carry their cells (e.g. `T10*20/4L` → four 10 × 20 mm cells on a
  40 × 20 mm page, `T40*15/2R` → two stacked cells).

### Fixed
- The calibrator preview keeps the real label proportions (a 50 × 50 mm roll used to be squashed to
  a 4:3 box, so circles looked like ellipses).
- A circle cell is now created with equal width and height, and editing one side keeps it round.
- Modals scroll their body instead of clipping content when it grows.

## [0.11.0] — 2026-11-13

### Added
- **Print density in the print dialog**: the B1 exposes the same 1–5 heat scale the vendor app
  calls *print density* (their model table stores `solubilitySetDefault: 3`, range 1–5). The new
  segmented control remembers the last value, defaults to what the printer reports, and travels
  with every single-label and sheet print (`POST /api/print|print-sheet` -> `density`).
- **Photo dithering per picture**: `Dither photo` on an image element halftones it (Floyd–Steinberg)
  before the 1-bit threshold, so gradients and shaded artwork keep their tone instead of collapsing
  into flat blobs. Text, barcodes and QR codes still use the sharp threshold.

### Changed
- The print summary spells out the density it will use (`2 copies · 2 labels · density 4`).

### Notes
- 203 dpi × 384 dots is the B1 hardware ceiling; the vendor's model table (`paccuracy: 8`,
  `paccuracyName: "203"`) confirms it. Their *sharper* look comes from the same 1-bit data with a
  different burn and halftone, which is exactly what these two controls now expose.

## [0.10.0] — 2026-11-13

### Added
- **Image cropping**: pictures get *Cover / Contain / Stretch*, a zoom factor and Crop X/Y offsets in
  millimetres, plus **Fill box / Centre / Reset** buttons and a live print-quality read-out
  (`source 640 × 480 px · ~813 dpi in this box — sharp`).
- **Unknown rolls**: when the printer cannot report the size and the catalogue has no entry for the
  roll code, the label bar shows `size unknown · set it`. One dialog stores the width, height, rows
  and gap for that roll code (`POST /api/roll-format`), so the next insertion is recognised instantly.
- **Format catalogue** tab now starts with the roll currently in the printer (with *Set / Edit size*)
  and its import button is English.

### Changed
- The canvas preview is again **the exact 1-bit bitmap** the printer receives (`png_print`, nearest
  neighbour), so what is on screen is literally what prints; the smoothed render is only used for
  thumbnails.
- Mirror mode centres, snaps and drags inside the first region only, and elements that spill past
  the cut are outlined instead of locked.

### Notes
- The NIIMBOT app keeps **no offline catalogue**: each roll's format is fetched from their cloud by
  the RFID code and cached per roll. `Import from the NIIMBOT app` imports everything it finds in
  that cache — opening the vendor app once with the new roll fills it in.

## [0.9.1] — 2026-11-13

### Changed
- **Text is rasterised 4× larger and averaged down before the 1-bit threshold**, so curves and
  stems survive the 203 dpi grid instead of stair-stepping (barcode modules stay pixel-identical).
- **The stage preview is a native 3× grayscale render** (`/api/preview` returns `png` for the
  screen and `png_print` for the exact print bitmap), so the designer shows smooth glyphs instead
  of a pixellated 1-bit upscale. PNG export still saves the print bitmap, and thumbnails are
  downscaled with LANCZOS.

Note: the B1 printhead is 203 dpi with 384 dots — 40 mm of label is 320 dots. That is the
hardware ceiling; the changes above get the most legible glyphs out of it.

## [0.9.0] — 2026-11-13

### Added
- **Print dialog**: every print now starts with a small dialog — pick the number of copies and,
  when the sheet holds more rows than the edited one, tick **Print the sheet** to run every row
  × copies (the summary shows the resulting label count before you commit).
- Status bars inside modals (icon library, fonts) moved into the modal footer so they hug and
  fill the bottom edge of the window.

### Fixed
- Modals that only need a little content no longer open with a large empty area: the print and
  prompt dialogs size themselves to their content.
- Remaining double-encoded dash in the sheet progress message; "Cancelled." is English now.

## [0.8.2] — 2026-11-13

### Added
- The executable prepares its own **`data` folder** next to `NiibotStudio.exe` on start-up and
  creates `niibot.db` (schema + built-in formats) when it is missing, so a fresh copy needs no
  setup. When that folder is read-only the app falls back to `%LOCALAPPDATA%\Niibot Studio\data`.
- The panel prints the resolved data folder on start, and `NIIBOT_DATA` still overrides it.

### Fixed
- Mirror mode now centres, snaps and drags inside the **first region only** — the editable band —
  instead of the whole label, so elements no longer jump into a generated region.
- An element that starts in the first region but spills past the cut stays editable and is
  outlined in orange (the overflow is clipped when printing) instead of being locked.

## [0.8.1] — 2026-11-13

### Changed
- The whole backend speaks English now: printer/ protocol errors, print-job messages, renderer
  warnings, IPP responses, CLI help and the installer/uninstaller scripts (the UI already did).
- Every modal uses the same inner padding (14 px top, 16 px sides and bottom) between the title
  bar, the content and the footer; single-field dialogs are compact instead of full height.
- Polish, German, French, Spanish and Italian translations cover the template gallery, the icon
  and font pickers and the new status strings.

### Fixed
- Print job messages showed mangled characters for accented text (double-encoded strings).
- Sheet job messages pluralise correctly ("Printed 1 label from the sheet.").
- The template gallery could show a scrollbar because the dialog height ignored its own padding
  and border.
- Static assets are served with `Cache-Control: no-cache`, so an updated app never keeps running
  a stale `app.js`/`style.css` from the browser cache.

## [0.8.0] — 2026-11-13

### Added
- **New label dialog is now a template gallery**: it shows the RFID-detected roll
  (size, label type, barcode) and a preview card for every template that fits it, with a
  **Show all sizes** toggle. Two new 40 × 30 mm built-ins for the standard B1 roll —
  *Product 40×30* and *Address 40×30*.
- **Element previews** (`POST /api/element-preview`): a single element is rendered on its own
  canvas, so layer thumbnails and drag ghosts no longer show neighbouring elements that happen
  to overlap the same box.
- **Autosave draft**: the design is written to `localStorage` 1.2 s after the last change and
  restored on reload; saving a label explicitly clears the draft.
- **Screenshots** of the English UI in `docs/screenshots/`.

### Changed
- The gallery dialog sizes itself to its content instead of always opening at a fixed height.

### Fixed
- Moving a label element showed the details of every element inside the bounding box; the drag
  ghost now carries only the dragged element's artwork.
- Translated strings were re-locked after switching back to English in the language menu.
- Remaining Polish strings ("Drukuj arkusz", "Wersz", column width/height prompts) translated.

## [0.7.0] — 2026-11-13

### Added
- **Data sheet view** (`Sheet` tab in the bottom-left of the work area) with columns bound
  to label elements, editable column names, free-form rows, sorting, CSV/XLSX import and
  printing straight from the sheet (`POST /api/print-sheet`, one job for the whole batch).
- **Resizable layout**: splitter for the properties panel and a splitter between the design
  and the sheet (double-click = full height); both sizes are remembered.
- **Auto-connect**: the remembered printer is looked up on start-up; once connected the RFID
  tag of the roll is read, the format is applied and the whole screen refreshes.
- **Compact sheet headers**: one type icon with a tooltip instead of the element dropdowns.
- **Mirror printing** for multi-region rolls: design only the first region, the remaining
  regions are filled at print time — either as a copy or as a vertical flip.
- **Save label as…** and **Save as template…** dialogs with a name field; user templates are
  stored in SQLite (`templates` table, `/api/templates`).
- **Icon library** with 9,308 searchable icons — 1,861 Lucide (ISC) and 7,447 Material Design
  Icons (Apache-2.0) — inserted as print-ready PNG images.
- **Font library**: all system fonts are indexed (114 families on a stock Windows 11) with a
  live preview picker, bold/italic detection and a drop-in folder (`data/fonts`).
- **Layer thumbnails** now show the element itself (ink-trimmed) instead of the label fragment.
- **Grid** (1 mm) and **snap-to-grid** toggles with alignment guides (edges and centres).
- Dragged elements carry their own artwork (the content visibly travels with the pointer).
- Version badge and changelog in **Settings → About**, `/api/app-info` endpoint.
- Standalone Windows build (`build-exe.ps1`, PyInstaller one-folder bundle).

### Changed
- Floating element toolbar is docked to the top of the work area instead of following the
  element.
- Test print moved from the left toolbar to **Settings → Printer** (connection row and the
  paired device row).
- Modals are fully opaque (the backdrop blur moved to a pseudo-element).

### Fixed
- Alignment guides missing for thin element types (the resize handle used to swallow the drag).
- Icon picker grid collapsed to zero-height rows.
- `DELETE /api/labels/{id}` returned a malformed response.

## [0.6.0] — 2026-11-12

### Added
- Photoshop-style **layers panel**: visibility eye, thumbnails, double-click rename,
  drag & drop ordering, and up/down/duplicate/delete toolbar.
- Editing **regions overlay**: row cut lines, gap strip, printhead-fit indicator.

## [0.5.0] — 2026-11-11

### Added
- **Windows printer queue** (IPP server + inbox class driver + installer script) that scales
  and crops incoming jobs to the format detected on the roll.
- `niibot/fit.py` fitting options (contain/cover/stretch, offsets, rotation, dithering).

## [0.4.0] — 2026-11-10

### Added
- Everything stored in a single **SQLite** database (`data/niibot.db`): settings, consumables,
  labels, profiles, uploads, print history, with automatic migration from the old JSON files.
- Label format catalogue: built-in presets, vendor-app import, manual entries.

## [0.3.0] — 2026-11-09

### Added
- Paper **format detection** from the vendor app cache (`ConsumablesTemplate-<barcode>`),
  including multi-row templates such as `T40*15/2R-460WHITE`.

## [0.2.0] — 2026-11-08

### Added
- Real printing over BLE: handshake, RFID read, bitmap encoding, page/print progress.
- Label designer in the browser with text, barcodes, QR codes, frames, lines and images.

## [0.1.0] — 2026-11-07

### Added
- Initial release: BLE scan/pair with a Niimbot B1, protocol framing, CLI tools.
