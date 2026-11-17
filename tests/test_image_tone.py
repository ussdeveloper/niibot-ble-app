"""Photo handling on a one-bit printer: print tone, and pictures that bleed off the label."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from niibot.render import render_label
from niibot.spec import Element, LabelSpec


def _photo(directory: Path, value: int, size: tuple[int, int] = (60, 40)) -> Path:
    path = directory / f"photo-{value}.png"
    Image.new("RGB", size, (value, value, value)).save(path)
    return path


def _render(element: Element, loader, width_mm: float = 40, height_mm: float = 20, dpi: int = 203):
    spec = LabelSpec(name="photo", width_mm=width_mm, height_mm=height_mm, elements=[element])
    return render_label(spec, dpi, 384, loader)


def _ink(rendered) -> int:
    """How many dots the printer would burn."""
    image = rendered.image.convert("L")
    return sum(1 for pixel in image.get_flattened_data() if pixel < 128)


def _two_band(directory: Path, dark: int = 100, light: int = 160) -> Path:
    """Half the picture sits just under the threshold, half just above it."""
    path = directory / "bands.png"
    image = Image.new("RGB", (60, 40), (dark, dark, dark))
    image.paste(Image.new("RGB", (60, 20), (light, light, light)), (0, 20))
    image.save(path)
    return path


class ImageBrightnessTest(unittest.TestCase):
    def test_missing_value_falls_back_to_neutral(self) -> None:
        element = Element(type="image", image_id="x", image_brightness=None)  # type: ignore[arg-type]
        self.assertEqual(element.image_brightness, 0.0)

    def test_out_of_range_is_clamped(self) -> None:
        self.assertEqual(Element(type="image", image_brightness=400).image_brightness, 100.0)
        self.assertEqual(Element(type="image", image_brightness=-400).image_brightness, -100.0)

    def test_brightness_moves_the_threshold_both_ways(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = _two_band(directory)
            loader = lambda _image_id: path  # noqa: E731
            base = _render(Element(type="image", image_id="p", x=2, y=2, w=20, h=16), loader)
            lighter = _render(
                Element(type="image", image_id="p", x=2, y=2, w=20, h=16, image_brightness=40), loader
            )
            darker = _render(
                Element(type="image", image_id="p", x=2, y=2, w=20, h=16, image_brightness=-40), loader
            )
            self.assertGreater(_ink(base), 0)
            self.assertEqual(_ink(lighter), 0)
            self.assertGreater(_ink(darker), _ink(base))

    def test_brightness_works_through_dithering(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = _photo(directory, 120)
            loader = lambda _image_id: path  # noqa: E731
            base = _render(
                Element(type="image", image_id="p", x=2, y=2, w=20, h=16, image_dither=True), loader
            )
            lighter = _render(
                Element(
                    type="image", image_id="p", x=2, y=2, w=20, h=16,
                    image_dither=True, image_brightness=60,
                ),
                loader,
            )
            self.assertLess(_ink(lighter), _ink(base))

    def test_full_brightness_prints_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = _photo(directory, 200)
            rendered = _render(
                Element(type="image", image_id="p", x=2, y=2, w=20, h=16, image_brightness=100),
                lambda _image_id: path,
            )
            self.assertEqual(_ink(rendered), 0)


class ImageBleedTest(unittest.TestCase):
    """A picture may hang over the edge of the label — the printer crops it, nothing breaks."""

    def test_picture_beyond_the_edge_is_clipped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = _photo(directory, 0, (200, 200))
            for element in (
                Element(type="image", image_id="p", x=-10, y=-8, w=60, h=40, image_fit="cover"),
                Element(type="image", image_id="p", x=30, y=10, w=60, h=40, image_fit="cover"),
            ):
                rendered = _render(element, lambda _image_id: path)
                self.assertEqual(rendered.width_px, 320)
                self.assertGreater(_ink(rendered), 0)

    def test_negative_position_keeps_the_ink_on_paper(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = _photo(directory, 0, (80, 80))
            alone = _render(
                Element(type="image", image_id="p", x=0, y=0, w=40, h=20, image_fit="cover"),
                lambda _image_id: path,
            )
            bleeding = _render(
                Element(type="image", image_id="p", x=-20, y=0, w=40, h=20, image_fit="cover"),
                lambda _image_id: path,
            )
            # Half the picture fell off the left edge, so half the dots are gone.
            self.assertLess(_ink(bleeding), _ink(alone))


if __name__ == "__main__":
    unittest.main()
