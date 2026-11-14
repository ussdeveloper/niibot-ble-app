"""Photo dithering on image elements."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from niibot.render import render_label
from niibot.spec import Element, LabelSpec


def _transitions(image: Image.Image, row: int) -> int:
    pixels = [image.getpixel((x, row)) for x in range(image.width)]
    return sum(1 for left, right in zip(pixels, pixels[1:]) if left != right)


class ImageDitherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        path = Path(self.folder.name) / "gradient.png"
        image = Image.new("L", (96, 96))
        for x in range(96):
            for y in range(96):
                image.putpixel((x, y), int(255 * x / 95))
        image.save(path)
        self.path = path

    def _render(self, dither: bool) -> Image.Image:
        spec = LabelSpec(
            width_mm=20,
            height_mm=20,
            elements=[
                Element(
                    type="image",
                    x=0,
                    y=0,
                    w=20,
                    h=20,
                    image_id="gradient",
                    image_fit="stretch",
                    image_dither=dither,
                )
            ],
        )
        rendered = render_label(spec, dpi=203, printhead_px=384, image_loader=lambda _id: self.path)
        return rendered.image

    def test_dither_turns_the_gradient_into_a_halftone(self) -> None:
        plain = self._render(False)
        dithered = self._render(True)
        middle = plain.height // 2
        self.assertGreater(_transitions(dithered, middle), 20)
        self.assertLessEqual(_transitions(plain, middle), 2)

    def test_dither_keeps_the_same_canvas_size(self) -> None:
        self.assertEqual(self._render(True).size, self._render(False).size)


if __name__ == "__main__":
    unittest.main()


class ImageFitTest(unittest.TestCase):
    """A resized image element must not lose its artwork to the new aspect ratio."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        path = Path(self.folder.name) / "square.png"
        Image.new("L", (64, 64), color=0).save(path)
        self.path = path

    def _render(self, fit: str, width_mm: float, height_mm: float) -> Image.Image:
        extra = {"image_fit": fit} if fit else {}
        spec = LabelSpec(
            width_mm=40,
            height_mm=30,
            elements=[
                Element(type="image", x=0, y=0, w=width_mm, h=height_mm, image_id="square", **extra)
            ],
        )
        return render_label(spec, dpi=203, printhead_px=384, image_loader=lambda _id: self.path).image

    def test_contain_letterboxes_a_stretched_box(self) -> None:
        rendered = self._render("contain", 40, 30)
        # The square art fits the 30 mm height, so the left/right bands stay blank.
        row = rendered.height // 2
        self.assertTrue(all(rendered.getpixel((x, row)) > 200 for x in range(2, 20)))

    def test_missing_fit_keeps_the_whole_picture(self) -> None:
        rendered = self._render("", 40, 30)
        row = rendered.height // 2
        self.assertTrue(all(rendered.getpixel((x, row)) > 200 for x in range(2, 20)))

    def test_cover_fills_the_box(self) -> None:
        rendered = self._render("cover", 40, 30)
        row = rendered.height // 2
        self.assertTrue(any(rendered.getpixel((x, row)) < 60 for x in range(rendered.width)))
