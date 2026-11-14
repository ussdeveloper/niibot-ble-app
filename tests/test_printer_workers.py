"""One worker per printer: its own protocol, its own lock, its own keep-alive.

The bugs these tests pin down: a global protocol setting that mislabelled every other printer,
the fallback family being tried against a printer that is not even advertising, and a single
ping loop that let one slow printer starve the link of another.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from niibot import server


class FakePrinter:
    """Stands in for a driver: records what happened, never touches Bluetooth."""

    def __init__(self, protocol: str, *, error: BaseException | None = None) -> None:
        self.protocol = protocol
        self.error = error
        self.connected = False
        self.ping_delay = 0.0
        self.pings = 0
        self.feed_mm = 0.0
        self.last_error = None
        self.info = SimpleNamespace(
            model_key="B1" if protocol == "niimbot" else "FICHERO",
            model_name="B1" if protocol == "niimbot" else "FICHERO",
            dpi=203,
            printhead_px=384 if protocol == "niimbot" else 96,
        )
        self.link = SimpleNamespace(
            address=None, name=None, last_error=None, connected=False
        )

    def set_geometry_override(self, *_args) -> None:
        pass

    async def connect(self, address: str):
        self.link.address = address
        if self.error is not None:
            raise self.error
        self.connected = True
        self.link.connected = True
        return self.info

    async def disconnect(self) -> None:
        self.connected = False
        self.link.connected = False

    async def refresh(self):
        return None

    async def keep_alive(self) -> bool:
        self.pings += 1
        if self.ping_delay:
            await asyncio.sleep(self.ping_delay)
        return self.connected


class WorkerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.created: list[FakePrinter] = []
        self.errors: dict[str, BaseException] = {}
        self.forgotten: list[str] = []
        self.protocols: dict[str, str] = {}
        self.rows: list[dict] = []
        patchers = [
            mock.patch.object(server, "make_printer", side_effect=self._make),
            mock.patch.object(server.store, "set_printer_protocol", side_effect=self._set_protocol),
            mock.patch.object(server.store, "printer_protocol", side_effect=lambda a: self.protocols.get(a)),
            mock.patch.object(server.store, "remember_printer", return_value=None),
            mock.patch.object(server.store, "list_printers", side_effect=lambda: list(self.rows)),
            mock.patch.object(server.store, "forget_printer", side_effect=self.forgotten.append),
            mock.patch.object(server.store, "save_settings", return_value=None),
            mock.patch.object(server.store, "claim_device_data", return_value=0),
            mock.patch.object(server, "_activate_connection", return_value=None),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._reset_state)
        self._reset_state()

    def _make(self, protocol: str) -> FakePrinter:
        printer = FakePrinter(protocol, error=self.errors.get(protocol))
        self.created.append(printer)
        return printer

    def _set_protocol(self, address: str, protocol: str) -> None:
        self.protocols[address] = protocol

    def _reset_state(self) -> None:
        server.state.connections.clear()
        server.state.printer_key = None
        server.state.last_scan = []
        server.state.maintain_links = True

    async def _stop_workers(self) -> None:
        for worker in list(server.state.connections.values()):
            await worker.stop()


class ProtocolAttributionTest(WorkerTestCase):
    def test_what_worked_for_this_printer_wins_over_the_global_setting(self) -> None:
        self.protocols["AA:BB"] = "luckprinter"
        self.assertEqual(
            server._protocol_for("AA:BB", {"printer_protocol": "niimbot"}), "luckprinter"
        )

    def test_the_global_setting_is_only_a_default_for_unknown_printers(self) -> None:
        self.assertEqual(
            server._protocol_for("CC:DD", {"printer_protocol": "niimbot"}), "niimbot"
        )

    def test_auto_falls_back_to_the_advertised_name(self) -> None:
        self.assertEqual(
            server._protocol_for("EE:FF", {"printer_protocol": "auto"}, "D11s_0A0B_BLE"),
            "luckprinter",
        )
        self.assertEqual(
            server._protocol_for("EE:FF", {"printer_protocol": "auto"}, "B1-G000000001"),
            "niimbot",
        )

    def test_the_worker_keeps_the_family_that_answered(self) -> None:
        self.errors["niimbot"] = RuntimeError("The printer did not answer the connect packet.")
        worker = server.PrinterWorker("11:22:33:44:55:66", "Fichero", "niimbot")

        async def scenario() -> bool:
            return await worker.connect_once()

        self.assertTrue(asyncio.run(scenario()))
        self.assertEqual(worker.protocol, "luckprinter")
        self.assertEqual(self.protocols["11:22:33:44:55:66"], "luckprinter")
        self.assertTrue(worker.connected)

    def test_an_absent_printer_does_not_pay_for_the_other_protocol(self) -> None:
        self.errors["niimbot"] = RuntimeError("Device with address 11:22 not found")
        worker = server.PrinterWorker("11:22:33:44:55:66", "B1", "niimbot")

        async def scenario() -> bool:
            return await worker.connect_once()

        self.assertFalse(asyncio.run(scenario()))
        self.assertEqual(len(self.created), 1)
        self.assertIn("not advertising", worker.last_error or "")


class IndependentKeepAliveTest(WorkerTestCase):
    def test_a_slow_ping_on_one_printer_does_not_delay_the_other(self) -> None:
        slow = server.PrinterWorker("AA:AA", "B1", "niimbot")
        quick = server.PrinterWorker("BB:BB", "Fichero", "luckprinter")
        slow.printer.connected = True
        quick.printer.connected = True
        slow.printer.ping_delay = 0.6

        async def scenario() -> tuple[bool, float]:
            slow_ping = asyncio.create_task(slow._ping_once())
            await asyncio.sleep(0.05)
            started = time.monotonic()
            alive = await quick._ping_once()
            elapsed = time.monotonic() - started
            await slow_ping
            return alive, elapsed

        alive, elapsed = asyncio.run(scenario())
        self.assertTrue(alive)
        self.assertLess(elapsed, 0.3, "the other printer's ping waited for the slow one")
        self.assertEqual(slow.printer.pings, 1)
        self.assertEqual(quick.printer.pings, 1)

    def test_locks_are_per_printer(self) -> None:
        first = server.PrinterWorker("AA:AA", "B1", "niimbot")
        second = server.PrinterWorker("BB:BB", "B1", "niimbot")
        self.assertIsNot(first.lock, second.lock)

    def test_a_lost_ping_drops_only_that_link(self) -> None:
        broken = server.PrinterWorker("AA:AA", "B1", "niimbot")
        healthy = server.PrinterWorker("BB:BB", "B1", "niimbot")
        broken.printer.connected = False
        healthy.printer.connected = True

        async def scenario() -> tuple[bool, bool]:
            return await broken._ping_once(), await healthy._ping_once()

        broken_ok, healthy_ok = asyncio.run(scenario())
        self.assertFalse(broken_ok)
        self.assertTrue(healthy_ok)


class DiscoveryTest(WorkerTestCase):
    def _row(self, address: str, name: str, protocol: str = "niimbot") -> dict:
        return {"address": address, "name": name, "protocol": protocol}

    def test_a_name_two_printers_share_never_matches(self) -> None:
        rows = [self._row("AA:AA", "D11s_1111_BLE"), self._row("BB:BB", "D11s_2222_BLE")]
        with mock.patch.object(server, "_known_printers", return_value=rows):
            counts = server._known_name_counts()
        self.assertEqual(counts["D11S_BLE"], 2)
        device = {"address": "CC:CC", "name": "D11s_3333_BLE"}
        self.assertIsNone(server._match_device(rows[0], [device], counts))

    def test_a_unique_name_survives_a_new_address(self) -> None:
        rows = [self._row("AA:AA", "B1-G000000001")]
        with mock.patch.object(server, "_known_printers", return_value=rows):
            counts = server._known_name_counts()
        device = {"address": "CC:CC", "name": "B1-G000000001"}
        self.assertEqual(server._match_device(rows[0], [device], counts), device)

    def test_two_rows_for_one_unit_produce_one_link(self) -> None:
        rows = [
            self._row("AA:AA", "B1-G000000001"),
            self._row("CC:CC", "B1-G000000001"),
        ]
        self.rows = rows
        devices = [{"address": "CC:CC", "name": "B1-G000000001"}]

        async def scenario() -> tuple[int, bool]:
            with mock.patch.object(server, "_known_printers", return_value=rows), mock.patch.object(
                server.store, "load_settings", return_value={}
            ):
                await server._ensure_connections(devices)
                worker = server.state.connections["cc:cc"]
                await worker.connect_once()
                # Discovery runs again: the unit is up, so its old address is merged away.
                await server._ensure_connections(devices)
            connected = worker.connected
            await self._stop_workers()
            return len(server.state.connections), connected

        count, connected = asyncio.run(scenario())
        self.assertEqual(count, 1, "the same unit must never get two BLE links")
        self.assertTrue(connected)
        self.assertEqual(set(self.forgotten), {"AA:AA"})

    def test_a_printer_out_of_range_is_waited_for_not_hammered(self) -> None:
        rows = [self._row("AA:AA", "B1-G000000001")]

        async def scenario() -> None:
            with mock.patch.object(server, "_known_printers", return_value=rows), mock.patch.object(
                server.store, "load_settings", return_value={}
            ):
                await server._ensure_connections([])
            await self._stop_workers()

        asyncio.run(scenario())
        worker = server.state.connections["aa:aa"]
        self.assertFalse(worker.in_range)
        self.assertFalse(worker.connected)
        self.assertEqual(len(self.created), 1)


if __name__ == "__main__":
    unittest.main()
