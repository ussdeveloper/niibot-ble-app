# Changelog

All notable changes to **Niibot Studio** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

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
