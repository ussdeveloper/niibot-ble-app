"""Item zones: one printable item that may be made of several outlines."""

from __future__ import annotations

import unittest

from niibot import zones
from niibot.models import Region, ZoneShape


def merged_zone() -> Region:
    return Region(
        shapes=[
            ZoneShape(kind="circle", x=0, y=0, w=20, h=20),
            ZoneShape(kind="rect", x=6, y=3, w=20, h=14),
        ]
    )


class RegionTest(unittest.TestCase):
    def test_bounding_box_follows_the_outlines(self) -> None:
        region = merged_zone().normalize()
        self.assertEqual((region.x, region.y), (0, 0))
        self.assertEqual((region.w, region.h), (26, 20))
        self.assertEqual(region.shape, "mixed")

    def test_single_outline_keeps_its_kind(self) -> None:
        region = Region(shapes=[ZoneShape(kind="circle", x=3, y=4, w=10, h=10)]).normalize()
        self.assertEqual(region.shape, "circle")
        self.assertEqual((region.x, region.y, region.w, region.h), (3, 4, 10, 10))

    def test_legacy_region_without_shapes_still_has_one_outline(self) -> None:
        region = Region.from_dict({"shape": "circle", "x": 1, "y": 2, "w": 8, "h": 8})
        outlines = region.outlines()
        self.assertEqual(len(outlines), 1)
        self.assertEqual(outlines[0].kind, "circle")
        self.assertEqual((outlines[0].x, outlines[0].w), (1, 8))

    def test_dict_round_trip_keeps_the_outlines(self) -> None:
        region = merged_zone().normalize()
        again = Region.from_dict(region.to_dict())
        self.assertEqual(len(again.shapes), 2)
        self.assertEqual(again.shape, "mixed")
        self.assertAlmostEqual(again.w, 26)


class OutlineImageTest(unittest.TestCase):
    def ink(self, image, x: int, y: int, radius: int = 3) -> bool:
        from PIL import Image

        box = (max(0, x - radius), max(0, y - radius),
               min(image.width, x + radius + 1), min(image.height, y + radius + 1))
        return Image.Image.getbbox(image.crop(box)) is not None

    def test_only_the_merged_border_is_printed(self) -> None:
        image = zones.outline_image(40, 30, [merged_zone().normalize()], 203)
        px_per_mm = image.width / 40
        # inside the circle, away from its edge: nothing printed
        self.assertFalse(self.ink(image, round(5 * px_per_mm), round(10 * px_per_mm), 2))
        # the outer left edge of the circle: printed
        self.assertTrue(self.ink(image, 1, round(10 * px_per_mm)))
        # where the two outlines overlap there is no extra line inside the item
        self.assertFalse(self.ink(image, round(8 * px_per_mm), round(10 * px_per_mm), 2))

    def test_label_border_can_be_switched_off(self) -> None:
        with_border = zones.outline_image(40, 30, [], 203, label_border=True)
        without = zones.outline_image(40, 30, [], 203, label_border=False)
        self.assertTrue(self.ink(with_border, 3, 3, 4))
        self.assertFalse(self.ink(without, 3, 3, 4))

    def test_image_matches_the_printer_pixel_size(self) -> None:
        image = zones.outline_image(48, 30, [], 203)
        self.assertEqual(image.size, (384, 240))


if __name__ == "__main__":
    unittest.main()
