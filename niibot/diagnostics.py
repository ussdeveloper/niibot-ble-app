"""Bluetooth diagnostics for devices we do not recognise.

A label printer sold under a house brand (Fichero, Karsten, OEM clones) advertises
nothing that identifies its protocol. This module connects with a plain BLE client,
dumps the GATT tree, reads what can be read and records every notification for a few
seconds — the command frames in those notifications are what identifies the protocol.
"""

from __future__ import annotations

import asyncio
import time


async def diagnose(address: str, seconds: float = 6.0) -> dict:
    """Inspect one BLE device. Returns ``report``, ``notifications`` and a ``verdict``."""
    from bleak import BleakClient

    seconds = max(1.0, min(30.0, float(seconds)))
    lines: list[str] = []
    notifications: list[dict] = []

    start = time.monotonic()
    async with BleakClient(address, timeout=25.0) as client:
        lines.append(f"device   {client.address}  {client.name or '(no name)'}")
        services = list(client.services)
        lines.append(f"services {len(services)}")
        notify_chars = []
        read_chars = []
        for service in services:
            lines.append(f"service  {service.uuid}  {service.description or ''}".rstrip())
            for char in service.characteristics:
                props = ",".join(char.properties)
                lines.append(f"  char   {char.uuid}  [{props}]  {char.description or ''}".rstrip())
                if "notify" in char.properties or "indicate" in char.properties:
                    notify_chars.append(char)
                if "read" in char.properties:
                    read_chars.append(char)
                for desc in char.descriptors:
                    lines.append(f"    desc {desc.uuid}")

        for char in read_chars[:12]:
            try:
                value = await asyncio.wait_for(client.read_gatt_char(char), timeout=2.0)
                raw = bytes(value)
                printable = "".join(chr(b) if 32 <= b < 127 else "." for b in raw)
                lines.append(f"read     {char.uuid}  {raw.hex()}  |{printable}|")
            except Exception as exc:  # noqa: BLE001 - a refused read is normal
                lines.append(f"read     {char.uuid}  failed: {exc}")

        def handler(char, data) -> None:  # type: ignore[no-untyped-def]
            notifications.append({
                "uuid": str(char.uuid),
                "hex": bytes(data).hex(),
                "at": round(time.monotonic() - start, 2),
            })

        subscribed = 0
        for char in notify_chars[:12]:
            try:
                await client.start_notify(char, handler)
                subscribed += 1
            except Exception as exc:  # noqa: BLE001
                lines.append(f"notify   {char.uuid}  failed: {exc}")
        lines.append(f"listening on {subscribed} characteristic(s) for {seconds:.0f} s")
        await asyncio.sleep(seconds)

    payload_bytes = sum(len(item["hex"]) // 2 for item in notifications)
    verdict = (
        "notifications received — the hex frames identify the protocol"
        if payload_bytes
        else "no data received — the device may need a command first, or it is classic Bluetooth (SPP) only"
    )
    return {
        "report": "\n".join(lines),
        "notifications": notifications,
        "services": len(services),
        "subscribed": subscribed,
        "bytes": payload_bytes,
        "verdict": verdict,
    }
