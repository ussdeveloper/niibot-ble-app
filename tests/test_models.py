"""The model table drives every dpi-dependent decision, so its numbers are pinned here."""

from __future__ import annotations

import asyncio
import unittest

from niibot.models import MODELS, MODEL_BY_ID, UNKNOWN_MODEL, model_for_id
from niibot.printer import NiimbotPrinter

from test_protocol import FakeLink


class ModelTableTests(unittest.TestCase):
    def test_b1_and_b1_pro_resolutions(self) -> None:
        b1 = model_for_id(4096)
        self.assertEqual((b1.name, b1.dpi, b1.printhead_px, b1.print_task), ("B1", 203, 384, "b1"))
        pro = model_for_id(4097)
        self.assertEqual((pro.name, pro.dpi, pro.printhead_px, pro.print_task), ("B1 Pro", 300, 567, "v4"))

    def test_other_known_devices(self) -> None:
        expected = {
            6912: ("B2 Pro", 300, 567),
            785: ("B21 Pro", 300, 591),
            256: ("B3S", 203, 576),
            6657: ("B4 Pro", 300, 1229),
            6400: ("M3", 300, 851),
        }
        for model_id, (name, dpi, head_px) in expected.items():
            with self.subTest(model_id=model_id):
                model = model_for_id(model_id)
                self.assertEqual((model.name, model.dpi, model.printhead_px), (name, dpi, head_px))

    def test_table_is_consistent(self) -> None:
        self.assertGreater(len(MODELS), 40)
        keys = [model.key for model in MODELS]
        self.assertEqual(len(keys), len(set(keys)), "model keys must be unique")
        for model in MODELS:
            self.assertTrue(model.ids, f"{model.key} needs at least one id")
            self.assertGreater(model.dpi, 0)
            self.assertGreater(model.printhead_px, 0)
            self.assertLess(model.printhead_px, 2048)
            self.assertIn(model.print_task, (None, "b1", "v4"))

    def test_id_collisions_resolve_to_the_first_entry(self) -> None:
        for model_id, model in MODEL_BY_ID.items():
            with self.subTest(model_id=model_id):
                self.assertIn(model_id, model.ids)

    def test_unknown_id_falls_back(self) -> None:
        self.assertIs(model_for_id(999999), UNKNOWN_MODEL)
        self.assertIs(model_for_id(None), UNKNOWN_MODEL)


class GeometryOverrideTests(unittest.TestCase):
    def _connect(self, printer: NiimbotPrinter, link: FakeLink) -> None:
        async def scenario() -> None:
            await printer.connect(link.address)

        asyncio.run(scenario())

    def test_override_applies_to_known_model(self) -> None:
        link = FakeLink()
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]
        printer.set_geometry_override(300, 567)
        self._connect(printer, link)
        self.assertEqual((printer.info.dpi, printer.info.printhead_px), (300, 567))
        self.assertEqual(printer.info.model_name, "B1")
        self.assertTrue(printer.info.geometry_override)

    def test_override_rescues_unknown_model(self) -> None:
        link = FakeLink()
        link.model_id = 4242  # not in the table - e.g. an OEM clone
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]
        self._connect(printer, link)
        self.assertEqual(printer.info.model_key, "UNKNOWN")
        self.assertEqual((printer.info.dpi, printer.info.printhead_px), (203, 384))
        self.assertFalse(printer.info.geometry_override)

        printer.set_geometry_override(300, 592, "v4")
        printer.refresh_geometry()
        self.assertEqual((printer.info.dpi, printer.info.printhead_px), (300, 592))
        self.assertEqual(printer.info.print_task, "v4")
        self.assertEqual(printer.info.model_name, "custom 300 dpi")
        self.assertTrue(printer.info.geometry_override)

    def test_override_can_be_cleared(self) -> None:
        link = FakeLink()
        printer = NiimbotPrinter(link)  # type: ignore[arg-type]
        printer.set_geometry_override(300, 567)
        self._connect(printer, link)
        printer.set_geometry_override(None, None, None)
        printer.refresh_geometry()
        self.assertEqual((printer.info.dpi, printer.info.printhead_px), (203, 384))
        self.assertFalse(printer.info.geometry_override)


if __name__ == "__main__":
    unittest.main()
