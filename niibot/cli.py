"""Command line helpers: scan, status check, one-off print, web server."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import webbrowser

from .bitmap import encode_image
from .printer import NiimbotPrinter
from .render import render_label
from .spec import Element, LabelSpec


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="niibot", description="Niimbot B1 - narzędzia")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="wyszukaj drukarki w pobliżu")
    scan.add_argument("--timeout", type=float, default=6.0)
    scan.add_argument("--all", action="store_true", help="pokaż wszystkie urządzenia BLE")

    info = sub.add_parser("info", help="połącz się i pokaż stan drukarki oraz papieru")
    info.add_argument("address")

    panel = sub.add_parser("panel", help="otwórz panel WWW (domyślnie http://127.0.0.1:8765)")
    panel.add_argument("--host", default="127.0.0.1")
    panel.add_argument("--port", type=int, default=8765)
    panel.add_argument("--no-browser", action="store_true")

    printer = sub.add_parser("print", help="wydrukuj prostą etykietę z tekstem")
    printer.add_argument("address")
    printer.add_argument("--text", default="Test")
    printer.add_argument("--width-mm", type=float, default=50.0)
    printer.add_argument("--height-mm", type=float, default=30.0)
    printer.add_argument("--font-pt", type=float, default=16.0)
    printer.add_argument("--copies", type=int, default=1)
    printer.add_argument("--density", type=int, default=3)
    printer.add_argument("--label-type", type=int, default=1)
    printer.add_argument("--barcode", default=None, help="dodaj kod kreskowy CODE128")
    printer.add_argument("--qr", default=None, help="dodaj kod QR z podanym tekstem")

    return parser.parse_args(argv)


async def _cmd_scan(args: argparse.Namespace) -> int:
    from .ble import BleLink

    devices = await BleLink.scan(args.timeout, args.all)
    if not devices:
        print("Nie znaleziono urządzeń. Sprawdź, czy drukarka jest włączona i sparowana.")
        return 1
    print(f"{'adres':<40} {'RSSI':>5}  nazwa")
    for device in devices:
        print(f"{device.address:<40} {('' if device.rssi is None else device.rssi):>5}  {device.name}")
    return 0


async def _cmd_info(args: argparse.Namespace) -> int:
    printer = NiimbotPrinter()
    try:
        info = await printer.connect(args.address)
    except Exception as exc:
        print(f"Błąd połączenia: {exc}", file=sys.stderr)
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
        print(f"Błąd połączenia: {exc}", file=sys.stderr)
        return 2
    try:
        info = printer.info
        rendered = render_label(spec, info.dpi, info.printhead_px)
        for warning in rendered.warnings:
            print(f"uwaga: {warning}", file=sys.stderr)
        encoded = encode_image(rendered.image)
        print(f"Druk: {encoded.rows} wierszy, {encoded.cols} px szerokości, {encoded.packet_count} pakietów")
        await printer.print_bitmap(
            encoded,
            copies=args.copies,
            density=args.density,
            label_type=args.label_type,
            on_progress=lambda value, **_: print(f"\r{value * 100:5.1f}%", end="", flush=True),
        )
        print("\nGotowe.")
    finally:
        await printer.disconnect()
    return 0


def _cmd_panel(args: argparse.Namespace) -> int:
    import uvicorn

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover
            pass
    print(f"Panel: {url}  (Ctrl+C kończy)")
    uvicorn.run("niibot.server:app", host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if not raw:
        raw = ["panel"]
    args = _parse_args(list(raw))
    if args.command == "scan":
        return asyncio.run(_cmd_scan(args))
    if args.command == "info":
        return asyncio.run(_cmd_info(args))
    if args.command == "print":
        return asyncio.run(_cmd_print(args))
    if args.command == "panel":
        return _cmd_panel(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
