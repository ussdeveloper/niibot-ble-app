"""Auto-connect triggers: a scan that sees the remembered printer connects right away."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from niibot import server
from niibot.ble import normalise_device_name


class NameMatcherTest(unittest.TestCase):
    def test_address_tail_is_ignored(self) -> None:
        self.assertEqual(normalise_device_name("D11s_0A0B_BLE"), "D11S_BLE")
        self.assertEqual(normalise_device_name("D11s_A1B2_BLE"), normalise_device_name("D11s_0A0B_BLE"))

    def test_serial_in_the_name_survives(self) -> None:
        # B1-G000000001 embeds a serial, not a hex tail: it must stay distinguishable.
        self.assertEqual(normalise_device_name("B1-G000000001"), "B1-G000000001")
        self.assertNotEqual(normalise_device_name("B1-G000000001"), normalise_device_name("B1-G999"))


class ScanTriggerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[str] = []
        patcher_remember = mock.patch.object(
            server,
            "_remembered_printer",
            return_value=("C9:48:8A:00:00:01", "luckprinter", ("D11s_0A0B_BLE",)),
        )
        patcher_once = mock.patch.object(server, "_auto_connect_once", side_effect=self._fake_attempt)
        patcher_task = mock.patch.object(server, "_auto_connect_loop", side_effect=self._fake_loop)
        self.addCleanup(patcher_remember.stop)
        self.addCleanup(patcher_once.stop)
        self.addCleanup(patcher_task.stop)
        patcher_remember.start()
        patcher_once.start()
        patcher_task.start()
        self.saved = (server.state.connected, server.state.reconnecting, server.state.auto_connect_task)
        server.state.reconnecting = False
        server.state.auto_connect_task = None
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        connected, reconnecting, task = self.saved
        server.state.reconnecting = reconnecting
        server.state.auto_connect_task = task
        if task is None:
            return
        server.state.auto_connect_task = None

    async def _fake_attempt(self):
        self.calls.append("attempt")
        return True, "connected"

    async def _fake_loop(self):
        self.calls.append("loop")

    async def _call(self, devices: list[dict]) -> None:
        with mock.patch.object(server.AppState, "connected", property(lambda _self: False)):
            server._connect_if_remembered_seen(devices)
            await asyncio.sleep(0)

    def _run(self, devices: list[dict]) -> None:
        asyncio.run(self._call(devices))

    def test_connects_when_the_address_is_seen(self) -> None:
        self._run([{"address": "c9:48:8a:00:00:01", "name": "something"}])
        self.assertIn("attempt", self.calls)

    def test_connects_when_the_name_matches_after_a_new_address(self) -> None:
        self._run([{"address": "de:ad:be:ef:00:01", "name": "D11s_55AA_BLE"}])
        self.assertIn("attempt", self.calls)

    def test_stays_quiet_for_other_devices(self) -> None:
        self._run([{"address": "C9:48:8A:00:00:02", "name": "B1-G000000001"}])
        self.assertEqual(self.calls, [])

    def test_does_nothing_while_reconnecting(self) -> None:
        server.state.reconnecting = True
        try:
            self._run([{"address": "C9:48:8A:00:00:01", "name": "D11s_0A0B_BLE"}])
        finally:
            server.state.reconnecting = False
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
