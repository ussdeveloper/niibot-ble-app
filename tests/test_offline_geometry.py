"""Off-line geometry: the model decides the head, not the last driver that happened to run.

A B1 Pro prints 300 dpi on a 567 px head. Showing the 203 dpi default of a fresh driver made the
preview bitmap a different size than the print and hid the strip the head cannot reach.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from niibot import server


class OfflineGeometryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.info = SimpleNamespace(
            model_key="UNKNOWN", model_name="unknown", dpi=203, printhead_px=384, geometry_override=False
        )
        self.printer = SimpleNamespace(info=self.info, connected=False)
        patches = [
            mock.patch.object(server.state, "printer", self.printer),
            mock.patch.object(server.state, "protocol", "niimbot"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _remember(self, model_key: str, protocol: str = "niimbot") -> None:
        row = {"address": "AA:BB", "model_key": model_key, "model_name": model_key, "protocol": protocol}
        patch_row = mock.patch.object(server, "_last_printer_row", return_value=row)
        patch_row.start()
        self.addCleanup(patch_row.stop)

    def test_b1_pro_head_is_300_dpi(self) -> None:
        self._remember("B1_PRO")
        self.assertEqual(server._geometry(), (300, 567))

    def test_plain_b1_stays_203_dpi(self) -> None:
        self._remember("B1")
        self.assertEqual(server._geometry(), (203, 384))

    def test_fichero_family_keeps_its_narrow_head(self) -> None:
        self._remember("FICHERO", protocol="luckprinter")
        self.assertEqual(server._geometry(), (203, 96))

    def test_hand_set_geometry_wins(self) -> None:
        self._remember("B1_PRO")
        self.info.geometry_override = True
        self.info.dpi, self.info.printhead_px = 203, 640
        self.assertEqual(server._geometry(), (203, 640))

    def test_unknown_printer_falls_back_to_the_driver(self) -> None:
        self._remember("UNKNOWN")
        self.assertEqual(server._geometry(), (203, 384))


if __name__ == "__main__":
    unittest.main()
