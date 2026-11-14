"""Gap calibration: the measuring strip and the form feed the Fichero needs."""

from __future__ import annotations

import unittest

from niibot import luckprinter as lp
from niibot.calibration import measuring_strip
from niibot.render import render_label


class MeasuringStripTest(unittest.TestCase):
    def test_scale_covers_the_label_height(self) -> None:
        spec = measuring_strip(12, 30)
        numbers = [element.text for element in spec.elements
                   if element.type == "text" and element.text.isdigit()]
        self.assertIn("0", numbers)
        self.assertIn("25", numbers)  # 30 mm tall: the last major tick is 25
        self.assertNotIn("35", numbers)

    def test_hint_text_fits_a_narrow_roll(self) -> None:
        narrow = measuring_strip(12, 30)
        wide = measuring_strip(40, 30)
        narrow_text = " ".join(e.text for e in narrow.elements if e.type == "text")
        wide_text = " ".join(e.text for e in wide.elements if e.type == "text")
        self.assertIn("GAP CAL.", narrow_text)
        self.assertNotIn("GAP CAL.", wide_text)

    def test_strip_prints_a_gap_label(self) -> None:
        spec = measuring_strip(12, 30)
        self.assertEqual(spec.label_type, 1)
        rendered = render_label(spec, 203, 96, None)
        self.assertEqual((rendered.width_px, rendered.height_px), (96, 240))
        self.assertGreater(sum(1 for pixel in rendered.image.convert("L").get_flattened_data() if pixel < 128), 500)


class FormFeedTest(unittest.TestCase):
    def test_acknowledged_form_feed_bytes(self) -> None:
        # 10 0C answers "OK"; 1D 0C is the silent variant used as a fallback.
        self.assertEqual(lp.FORM_FEED_ACK, b"\x10\x0c")
        self.assertEqual(lp.FORM_FEED, b"\x1d\x0c")

    def test_paper_type_for_gap_labels(self) -> None:
        # The panel's label type 1 (labels with gaps) must become the family's 0 = gap.
        self.assertEqual(lp.paper_type_from_label_type(1), 0)


if __name__ == "__main__":
    unittest.main()
