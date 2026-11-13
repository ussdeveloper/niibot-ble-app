# Changelog

All notable changes to **Niibot Studio** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

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
