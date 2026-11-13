"""Copying the design from the first calibrated cell into the remaining cells."""

from __future__ import annotations

import unittest

from niibot.sheet import apply_cell_mirror
from niibot.spec import Element, LabelSpec

CELLS = [
    {"shape": "rect", "x": 0, "y": 0, "w": 20, "h": 20},
    {"shape": "rect", "x": 20, "y": 0, "w": 20, "h": 20},
]


def spec() -> LabelSpec:
    return LabelSpec(
        width_mm=40,
        height_mm=20,
        elements=[Element(type="text", text="A", x=2, y=2, w=16, h=6)],
    )


class CellMirrorTest(unittest.TestCase):
    def test_copies_cell_one_into_the_next_cell(self) -> None:
        result = apply_cell_mirror(spec(), CELLS)
        self.assertEqual(len(result.elements), 2)
        copy = result.elements[1]
        self.assertAlmostEqual(copy.x, 22.0)
        self.assertAlmostEqual(copy.y, 2.0)
        self.assertEqual(copy.text, "A")

    def test_flip_turns_the_copy_upside_down_inside_its_cell(self) -> None:
        result = apply_cell_mirror(spec(), CELLS, flip=True)
        copy = result.elements[1]
        self.assertAlmostEqual(copy.y, 20 - 2 - 6)
        self.assertEqual(copy.rotation, 180)

    def test_smaller_cell_scales_the_copy(self) -> None:
        cells = [
            {"shape": "rect", "x": 0, "y": 0, "w": 20, "h": 20},
            {"shape": "rect", "x": 20, "y": 0, "w": 10, "h": 10},
        ]
        result = apply_cell_mirror(spec(), cells)
        copy = result.elements[1]
        self.assertAlmostEqual(copy.w, 8.0)
        self.assertAlmostEqual(copy.h, 3.0)
        self.assertAlmostEqual(copy.x, 21.0)

    def test_single_cell_leaves_the_spec_alone(self) -> None:
        result = apply_cell_mirror(spec(), CELLS[:1])
        self.assertEqual(len(result.elements), 1)


if __name__ == "__main__":
    unittest.main()
