"""A label that arrives with nulls must still render.

The inspector used to send `rotation: null` (JavaScript turns `NaN` into `null`), and every
keystroke failed with `Input should be a valid integer` — the text could not be typed at all.
"""

from __future__ import annotations

import unittest

from niibot.render import render_label
from niibot.spec import LabelSpec
from niibot.store import upload_path


def _label(element: dict) -> LabelSpec:
    return LabelSpec.model_validate(
        {"width_mm": 40, "height_mm": 20, "elements": [element]}
    )


class NullFieldTest(unittest.TestCase):
    def test_rotation_null_becomes_zero(self) -> None:
        spec = _label({"type": "text", "text": "teees3333", "rotation": None})
        self.assertEqual(spec.elements[0].rotation, 0)

    def test_a_half_filled_text_element_gets_usable_defaults(self) -> None:
        spec = _label({
            "type": "text",
            "text": "teees3333",
            "font": None,
            "font_size_pt": None,
            "line_spacing": 0,
            "h": 0,
            "x": None,
            "y": 1,
        })
        element = spec.elements[0]
        self.assertEqual(element.font, "Arial")
        self.assertGreaterEqual(element.font_size_pt, 1)
        self.assertGreaterEqual(element.line_spacing, 0.6)
        self.assertGreater(element.h, 0)
        self.assertEqual(element.x, 0)

    def test_everything_null_renders(self) -> None:
        spec = _label({
            "type": "text",
            "text": "x",
            "rotation": None,
            "font": None,
            "line_spacing": None,
            "thickness_mm": None,
            "image_zoom": None,
            "bold": None,
            "align": None,
            "valign": None,
        })
        rendered = render_label(spec, 203, 384, upload_path)
        self.assertEqual(rendered.image.size, (320, 160))

    def test_rotation_is_still_rounded_to_quarter_turns(self) -> None:
        self.assertEqual(_label({"type": "text", "rotation": 91}).elements[0].rotation, 90)
        self.assertEqual(_label({"type": "text", "rotation": -90}).elements[0].rotation, 270)


if __name__ == "__main__":
    unittest.main()
