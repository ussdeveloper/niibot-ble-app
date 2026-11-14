"""QR and barcode printable minimums — a code must never be printed with smeared modules."""

from __future__ import annotations

import unittest

from niibot.codes import (
    GOOD_MODULE_DOTS,
    HARD_MODULE_DOTS,
    barcode_module_count,
    minimum_size,
    qr_module_count,
)
from niibot.fit import mm_to_px
from niibot.render import render_label
from niibot.spec import Element, LabelSpec


def _render(element: Element, width_mm: float = 40, height_mm: float = 30, dpi: int = 203):
    spec = LabelSpec(name="code", width_mm=width_mm, height_mm=height_mm, elements=[element])
    return render_label(spec, dpi, 384, None)


def _ink(rendered) -> int:
    grayscale = rendered.image.convert("L")
    return sum(1 for pixel in grayscale.get_flattened_data() if pixel < 128)


class CodeLimitTest(unittest.TestCase):
    def test_qr_minimum_scales_with_dpi(self) -> None:
        slow = minimum_size("qr", 203, "https://example.com/p/12345")
        fast = minimum_size("qr", 300, "https://example.com/p/12345")
        self.assertTrue(slow["square"])
        self.assertLess(fast["min_w_mm"], slow["min_w_mm"])
        # One dot per module is the floor; two dots per module is the safe size.
        self.assertAlmostEqual(slow["min_w_mm"], slow["modules"] * slow["hard_module_mm"], delta=0.2)
        self.assertGreater(slow["recommended_w_mm"], slow["min_w_mm"])

    def test_barcode_minimum_grows_with_data(self) -> None:
        short = minimum_size("barcode", 203, "1234", barcode_format="code128")
        long = minimum_size("barcode", 203, "1234567890123456", barcode_format="code128")
        self.assertFalse(short["square"])
        self.assertGreater(long["min_w_mm"], short["min_w_mm"])
        self.assertGreater(long["modules"], short["modules"])

    def test_qr_below_one_dot_per_module_is_refused(self) -> None:
        # 29 modules need 29 px; a 2 mm box is 16 px at 203 dpi — the box cannot hold the matrix.
        element = Element(type="qr", x=0, y=0, w=2, h=2, text="https://example.com/p/12345")
        rendered = _render(element)
        self.assertEqual(_ink(rendered), 0)
        self.assertTrue(any("was not printed" in warning for warning in rendered.warnings))

    def test_qr_crisp_modules_at_a_comfortable_size(self) -> None:
        element = Element(type="qr", x=0, y=0, w=20, h=20, text="https://example.com/p/12345")
        rendered = _render(element)
        self.assertGreater(_ink(rendered), 0)
        self.assertEqual(rendered.warnings, [])
        modules = qr_module_count("https://example.com/p/12345")
        self.assertGreaterEqual(round(mm_to_px(20, 203)) // modules, GOOD_MODULE_DOTS)

    def test_qr_with_single_dot_modules_warns_but_prints(self) -> None:
        # 21 modules in 3 mm at 203 dpi is about 24 px: one dot per module and no quiet zone.
        element = Element(type="qr", x=0, y=0, w=3, h=3, text="https://")
        self.assertEqual(qr_module_count("https://"), 21)
        self.assertGreaterEqual(round(mm_to_px(3, 203)) // 21, HARD_MODULE_DOTS)
        rendered = _render(element)
        self.assertGreater(_ink(rendered), 0)
        self.assertTrue(any("one dot" in warning for warning in rendered.warnings))

    def test_barcode_below_one_dot_per_module_is_refused(self) -> None:
        element = Element(
            type="barcode", x=2, y=2, w=4, h=12, text="1234567890", barcode_format="code128"
        )
        rendered = _render(element)
        self.assertEqual(_ink(rendered), 0)
        self.assertTrue(any("was not printed" in warning for warning in rendered.warnings))

    def test_barcode_wide_enough_prints(self) -> None:
        element = Element(
            type="barcode", x=2, y=2, w=36, h=12, text="1234567890", barcode_format="code128"
        )
        rendered = _render(element)
        self.assertGreater(_ink(rendered), 0)
        self.assertEqual(rendered.warnings, [])
        modules = barcode_module_count("code128", "1234567890")
        self.assertGreaterEqual(round(mm_to_px(36, 203)) // modules, GOOD_MODULE_DOTS)


if __name__ == "__main__":
    unittest.main()
