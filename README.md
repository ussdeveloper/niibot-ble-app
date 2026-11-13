# Niibot Studio

Local label printing for **Niimbot B1** thermal printers over Bluetooth LE — no cloud, no
account, no vendor app. It is a small Python/FastAPI service with a full label designer in
the browser and an optional Windows print queue.

* **Pair and connect** to the printer over BLE (scan, remember the device, reconnect).
* **Paper detection**: lid state, paper presence, RFID tag of the roll (paper type, roll
  barcode, print counter, remaining labels).
* **Label designer**: text, barcodes (Code128/39/EAN/UPC/ITF), QR codes, frames, lines,
  images, rotation, alignment, auto-fit.
* **Fonts**: every installed system font is indexed (114 families on a stock Windows 11),
  with a live preview picker and a drop-in `data/fonts` folder for your own `.ttf`/`.otf`.
* **Icons and cliparts**: 9,308 searchable icons — 1,861 [Lucide](https://lucide.dev) (ISC)
  and 7,447 [Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache-2.0) —
  inserted as print-ready PNGs.
* **Data sheet**: Excel-like grid where each column is bound to an element, with batch
  printing and *mirror* mode for multi-region rolls.
* **Photoshop-style layers**: visibility, thumbnails, rename, drag & drop ordering.
* **Grid, snapping and alignment guides** while dragging.
* **Windows printer**: registers itself as a normal print queue (IPP), scaling every job to
  the format read from the roll.
* **One SQLite file** holds settings, formats, labels, templates, images and print history.
* **CLI** for quick hardware checks.
* **Standalone Windows build** (`build-exe.ps1`) — no Python needed on the target machine.

Supported protocol families: B1/B21/D11 (`b1` task, 203 dpi, 384 px head) and newer 300 dpi
devices (`v4`, e.g. B1 Pro / B21 Pro).

---

## Quick start

```powershell
# from the project directory
.\\start.bat          # creates .venv, installs requirements, opens the panel
```

or manually:

```powershell
python -m venv .venv
.\\.venv\\Scripts\\pip install -r requirements.txt
.\\.venv\\Scripts\\python -m niibot panel
```

The panel opens at <http://127.0.0.1:8765>. Requirements: Windows 10/11 with Bluetooth LE,
Python 3.10+.

### Standalone executable

```powershell
powershell -ExecutionPolicy Bypass -File build-exe.ps1
```

The result is `dist\\NiibotStudio\\NiibotStudio.exe` — a self-contained folder (Python,
dependencies, web assets). `--port` and `--no-browser` work the same as with the module.
The version comes from `niibot/version.py`; see [CHANGELOG.md](CHANGELOG.md).

### Windows printer (one-time, as administrator)

```powershell
powershell -ExecutionPolicy Bypass -File install-printer.ps1
```

After that the printer shows up in **Settings → Bluetooth & devices → Printers** and any
application can print to it.

---

## Pairing

1. Turn the printer on (hold the power button).
2. In Windows: **Settings → Bluetooth & devices → Add device → Bluetooth**, pick the entry
   starting with `B1…` (PIN `0000` or `1234` if asked).
3. In the panel click **Scan** and pick the printer from the list.

The connection uses GATT directly (service `e7810a71-73ae-499d-8c15-faa9aef0c3f2`,
characteristic `bef8d6c9-9c21-4c9e-b632-bd58c1009f9f`), so no drivers or COM ports are needed.

---

## Interface

| Area | What it does |
| --- | --- |
| menu bar | **File / Edit / View / Settings / Help** — save (also *Save as…*, *Save as template…*), undo, view toggles, settings |
| left toolbar | add elements, icon library, undo/redo, save, PNG export, print, **grid** and **snap** toggles |
| floating toolbar | actions on the selected element (centre, rotate, reorder, duplicate, delete) |
| right panel | **Properties** of the selection and the **Layers** list |
| bottom-left tabs | switch the work area between **Design** and **Sheet** |
| status bar | connection, paper status, format size, density, database |

Keyboard: `Ctrl+Z/Y` undo/redo, `Ctrl+S` save, `Ctrl+P` print, `Ctrl+D` duplicate,
`Delete` remove, arrows nudge by 1 mm (`Shift` = 0.2 mm), `G` grid, `S` snapping,
`Alt` while dragging = free movement, `Shift` while dragging = one axis.

### Canvas, grid and guides

Dragging is live: the element's own artwork lifts off the page and follows the pointer, the
rest of the preview dims. While dragging, edges and centres of other elements as well as the
label edges/centre produce subtle alignment guides (centre lines use a different colour).
With snapping on, positions snap to the 1 mm grid and to those guides.

### Sheet view

The **Sheet** tab treats the label as data:

* every value-bearing element (text, barcode, QR) becomes a **column**; the header holds an
  editable column name, the element type and a selector to re-bind the column,
* **row 1 is the current label** — editing it changes the design immediately,
* further rows are queued records; **Print sheet** prints row 1 and then every row in one job,
* **Mirror** (enabled when the roll has more than one region, e.g. `T40*15/2R`): you design
  only the first region and the remaining regions are filled at print time, either as a copy
  or as a vertical flip.

The sheet lives inside the label document, so it is saved, loaded and versioned with it.

### Layers

The list is reversed like in Photoshop — the top row is the topmost artwork. Each row has a
visibility eye, an ink-trimmed thumbnail of that element, the name (double-click to rename),
a drag handle and the ordering toolbar underneath.

### Fonts and icons

* **Properties → Font** (or **Edit → Font…**) opens a searchable list of all detected
  families rendered in their own typeface; the renderer loads the very same font file.
* **Left toolbar → icons** (or **Edit → Insert icon…**) opens the icon browser with source
  filter and tag search; picking an icon rasterises the SVG to a 384 px PNG and uploads it
  into the database.

---

## Data: one SQLite file

Everything lives in **`data\\niibot.db`** (SQLite, WAL):

| Table | Contents |
| --- | --- |
| `settings` | key/value application state |
| `consumables` | label formats (built-in, imported from the vendor app, manual) |
| `labels` | saved label documents (JSON) |
| `templates` | user templates created with *Save as template…* |
| `profiles` | remembered geometry per physical roll |
| `uploads` | images and icons as BLOBs |
| `print_jobs` | print history → statistics |
| `schema_meta` | schema version |

Older JSON files (`labels.json`, `profiles.json`, `settings.json`) are migrated once on start.

---

## Paper detection and formats

The B1 firmware (13.02, protocol 204) answers `0x00` to `GetPaperInfo (0x59)`, so the printer
itself cannot report the format. The vendor app solves this with the RFID tag plus its cloud
API, and caches the answer in `localStorage`:

* `ConsumablesInfo-<serial>` — `allowNum`, `actualNum`, `materialUsed`, `statusCode`
* `ConsumablesTemplate-<barcode>` — width, height, paper type, rows, capacity

Niibot Studio reads that cache (`niibot/consumables.py`), stores the formats in SQLite and
matches the roll barcode read from the tag. SKU convention: `T<w>*<h>[/NR]-<count><colour>`,
where `2R` means two rows and the template height is `h × rows`. The roll used during
development is `T40*15/2R-460WHITE`: two 40 × 15 mm labels on a 40 × 30 mm template.

Without a known format, pick a preset in **Settings → Label** or enter the size manually;
the size is remembered per roll (`profiles`).

---

## Windows print queue (IPP)

Windows does not allow unsigned kernel drivers, so the app exposes an IPP endpoint
(`POST /ipp/print`) and `install-printer.ps1` registers a queue that uses the inbox
**Microsoft IPP Class Driver**. Jobs are decoded (PWG Raster / PDF / JPEG), fitted to the
detected format with `niibot/fit.py` (contain / cover / stretch, offsets, rotation, margin,
threshold, dithering, invert) and printed over BLE.

```powershell
# manual installation, elevated PowerShell
powershell -ExecutionPolicy Bypass -File install-printer.ps1
powershell -ExecutionPolicy Bypass -File uninstall-printer.ps1
```

---

## CLI

```powershell
python -m niibot scan                 # list nearby printers
python -m niibot info <address>       # model, firmware, paper, RFID
python -m niibot print --text "Hi"    # quick print
python -m niibot panel                # web UI
```

---

## How it works (protocol)

* Frame: `55 55 <cmd> <len> <data…> <crc> AA AA`, `crc` = XOR of cmd, len and data;
  the connect frame carries a `0x03` prefix.
* The B1 requires an exact handshake (`0xA5`, `0x40` for `08 0b 0d 0a 07 03 0c 09`, `0xDC[04]`),
  otherwise it accepts commands but never prints.
* Print job: `PrintStart`, `PageStart 0x03`, `SetPageSize 0x13`, rows as `0x84`/`0x85`
  packets, then poll `0xA3` until the page counter reaches the copy count and send `0xF3`.
* The B1 drops rows at full speed — the transport paces writes (10 ms) and bundles frames.

---

## Project layout

```
niibot/            protocol, BLE transport, printer client, renderer, IPP, storage, server
web/               designer UI (index.html, style.css, app.js) + vendored icon libraries
tests/             protocol + storage/IPP tests (python -m unittest discover -s tests)
data/              SQLite database, uploads, font index, custom fonts (git-ignored)
install-printer.ps1 / uninstall-printer.ps1
build-exe.ps1      PyInstaller bundle
CHANGELOG.md
```

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Printer not found | check Windows pairing, then **Scan**; other BLE apps may hold the connection |
| Connects but nothing prints | run **Test print** in Settings → Printer; inspect `/api/log` for frame errors |
| Print shifted against the cuts | run **Paper calibration** in Settings → Printer |
| Everything is cut off on the right | the label is wider than the printhead — the stage shows the limit |
| Port already in use | `python -m niibot panel --port 8766` |

---

## Credits

* Protocol knowledge from the reverse-engineered Niimbot community projects
  (`niimbot-web-bluetooth`, `niimbluelib`, `niimprint`).
* Icons: [Lucide](https://lucide.dev) (ISC) and
  [Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache-2.0).
* UI inspiration: VS Code shell, SunCore/BUI colour language.

## License

**MIT** — free for personal and commercial use, modification and redistribution; see
[LICENSE](LICENSE). Bundled third-party components keep their own permissive licences
(ISC, Apache-2.0, BSD, MIT) and are listed with attribution in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Fonts are not redistributed — the app uses
the fonts installed on your machine or the ones you drop into `data/fonts`.
