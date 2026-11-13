# Changelog

All notable changes to **Niibot Studio** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

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
