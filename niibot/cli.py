"""Command line helpers: scan, status check, one-off print, web server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path as _Path

from .bitmap import encode_planes
from .printer import NiimbotPrinter
from .render import render_label
from .spec import Element, LabelSpec
from .store import DATA_DIR
from .version import __version__ as APP_VERSION


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="niibot", description="Niimbot B1 - tools")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="look for nearby printers")
    scan.add_argument("--timeout", type=float, default=6.0)
    scan.add_argument("--all", action="store_true", help="show every BLE device")

    info = sub.add_parser("info", help="connect and show the printer and paper state")
    info.add_argument("address")

    diagnose = sub.add_parser("diagnose", help="inspect any BLE device (services + notification traffic)")
    diagnose.add_argument("address")
    diagnose.add_argument("--seconds", type=float, default=6.0)

    panel = sub.add_parser("panel", help="open the web panel (http://127.0.0.1:8765 by default)")
    panel.add_argument("--host", default="127.0.0.1")
    panel.add_argument("--port", type=int, default=8765)
    panel.add_argument("--no-browser", action="store_true")

    printer = sub.add_parser("print", help="print a simple text label")
    printer.add_argument("address")
    printer.add_argument("--text", default="Test")
    printer.add_argument("--width-mm", type=float, default=50.0)
    printer.add_argument("--height-mm", type=float, default=30.0)
    printer.add_argument("--font-pt", type=float, default=16.0)
    printer.add_argument("--copies", type=int, default=1)
    printer.add_argument("--density", type=int, default=3)
    printer.add_argument("--label-type", type=int, default=1)
    printer.add_argument("--barcode", default=None, help="add a CODE128 barcode")
    printer.add_argument("--qr", default=None, help="add a QR code with the given text")
    printer.add_argument(
        "--ink",
        choices=("black", "red"),
        default="black",
        help="print in black, or in red on red/black two-colour stock",
    )

    return parser.parse_args(argv)


async def _cmd_scan(args: argparse.Namespace) -> int:
    from .ble import BleLink

    devices = await BleLink.scan(args.timeout, args.all)
    if not devices:
        print("No devices found. Check that the printer is on and paired.")
        return 1
    print(f"{'address':<40} {'RSSI':>5}  {'kind':<14} name")
    for device in devices:
        kind = "label printer" if device.niimbot else "ble device"
        print(f"{device.address:<40} {('' if device.rssi is None else device.rssi):>5}  {kind:<14} {device.name}")
    if not any(device.niimbot for device in devices):
        print("\nNo printer from the Niimbot family found. `niibot diagnose <address>` inspects any device.")
    return 0


async def _cmd_diagnose(args: argparse.Namespace) -> int:
    from .diagnostics import diagnose

    try:
        result = await diagnose(args.address, args.seconds)
    except Exception as exc:
        print(f"Diagnose failed: {exc}", file=sys.stderr)
        return 2
    print(result["report"])
    for item in result["notifications"][:60]:
        print(f"  {item['at']:>6}s  {item['uuid']}  {item['hex']}")
    print(f"\n{result['verdict']}")
    return 0


async def _cmd_info(args: argparse.Namespace) -> int:
    printer = NiimbotPrinter()
    try:
        info = await printer.connect(args.address)
    except Exception as exc:
        print(f"Connection error: {exc}", file=sys.stderr)
        return 2
    try:
        paper = await printer.refresh()
        print(json.dumps({"info": info.__dict__, "paper": paper.to_dict()}, ensure_ascii=False, indent=2))
    finally:
        await printer.disconnect()
    return 0


async def _cmd_print(args: argparse.Namespace) -> int:
    elements = [
        Element(
            type="text",
            x=2,
            y=2,
            w=args.width_mm - 4,
            h=max(6.0, args.height_mm * 0.45),
            text=args.text,
            font_size_pt=args.font_pt,
            bold=True,
            align="center",
            auto_fit=True,
        )
    ]
    if args.barcode:
        elements.append(
            Element(
                type="barcode",
                x=2,
                y=args.height_mm - 10,
                w=args.width_mm - 4,
                h=8,
                text=args.barcode,
                barcode_format="code128",
            )
        )
    if args.qr:
        elements.append(
            Element(type="qr", x=(args.width_mm - 14) / 2, y=args.height_mm - 15, w=14, h=14, text=args.qr)
        )
    if args.ink != "black":
        elements = [element.model_copy(update={"ink": args.ink}, deep=True) for element in elements]
    spec = LabelSpec(
        name="CLI",
        width_mm=args.width_mm,
        height_mm=args.height_mm,
        elements=elements,
    )

    printer = NiimbotPrinter()
    try:
        await printer.connect(args.address)
    except Exception as exc:
        print(f"Connection error: {exc}", file=sys.stderr)
        return 2
    try:
        info = printer.info
        rendered = render_label(spec, info.dpi, info.printhead_px)
        for warning in rendered.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        encoded = encode_planes(rendered.black, rendered.red)
        print(f"Print: {encoded.rows} rows, {encoded.cols} px wide, {encoded.packet_count} packets")
        await printer.print_bitmap(
            encoded,
            copies=args.copies,
            density=args.density,
            label_type=args.label_type,
            on_progress=lambda value, **_: print(f"\r{value * 100:5.1f}%", end="", flush=True),
        )
        print("\nDone.")
    finally:
        await printer.disconnect()
    return 0


#: Chromium-based browsers that can open the panel as a window of its own (no tabs, no address bar).
_BROWSER_CANDIDATES = (
    r"Microsoft\Edge\Application\msedge.exe",
    r"Google\Chrome\Application\chrome.exe",
)


def _find_app_browser() -> str | None:
    roots = [
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for root in roots:
        if not root:
            continue
        for relative in _BROWSER_CANDIDATES:
            candidate = _Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return None


def _open_panel_window(url: str) -> str:
    """Show the panel in its own app window; fall back to the default browser."""
    browser = _find_app_browser()
    if browser is None:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - depends on the machine
            pass
        return "default browser"
    # Its own profile folder, so the window keeps its own settings and is a separate app
    # entry (it does not disturb the user's normal browser session).
    profile = _Path(DATA_DIR) / "window"
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - unwritable data folder
        return "default browser"
    try:
        subprocess.Popen(
            [
                browser,
                f"--app={url}",
                f"--user-data-dir={profile}",
                "--window-size=1440,920",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=Translate,MediaRouter",
            ],
            close_fds=True,
        )
    except OSError:  # pragma: no cover - browser disappeared between the check and the spawn
        webbrowser.open(url)
        return "default browser"
    return f"{_Path(browser).stem} app window"


def _open_when_ready(url: str, host: str, port: int, timeout: float = 25.0) -> None:
    """Wait for the server to accept connections, then open the window (dot not white page)."""
    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((target, port), timeout=0.4):
                break
        except OSError:
            time.sleep(0.15)
    where = _open_panel_window(url)
    print(f"Window: {where}")


def _cmd_panel(args: argparse.Namespace) -> int:
    import uvicorn

    url = f"http://{args.host}:{args.port}"
    print(f"Niibot Studio {APP_VERSION} - data folder: {DATA_DIR}")
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url, args.host, args.port), daemon=True).start()
    print(f"Panel: {url}  (Ctrl+C stops it)")
    # Import the app object directly: the frozen EXE has no importable module path
    # for uvicorn's string form ("niibot.server:app").
    from .server import app as asgi_app

    uvicorn.run(asgi_app, host=args.host, port=args.port, log_level="info")
    return 0


def _prepare_data() -> bool:
    """Create data/ next to the executable and initialise the database on first run."""
    from . import store

    try:
        store.ensure_dirs()
    except Exception as exc:  # pragma: no cover - depends on the machine
        print(f"Cannot prepare the data folder ({store.DATA_DIR}): {exc}", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if not raw:
        raw = ["panel"]
    args = _parse_args(list(raw))
    if not _prepare_data():
        return 3
    if args.command == "scan":
        return asyncio.run(_cmd_scan(args))
    if args.command == "info":
        return asyncio.run(_cmd_info(args))
    if args.command == "diagnose":
        return asyncio.run(_cmd_diagnose(args))
    if args.command == "print":
        return asyncio.run(_cmd_print(args))
    if args.command == "panel":
        return _cmd_panel(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
