import io
import unittest
from PIL import Image

from core.photo_layout import fit_inside, feed_height, image_size


class PhotoLayoutTests(unittest.TestCase):
    def test_landscape_completely_visible(self):
        self.assertEqual(fit_inside(1920, 1080, 676, 500), (676, 380))
        self.assertEqual(feed_height(1920, 1080), 380)

    def test_portrait_keeps_original_ratio(self):
        self.assertEqual(fit_inside(1080, 1920, 676, 950), (534, 950))
        self.assertEqual(feed_height(1080, 1920), 950)

    def test_square_photo(self):
        self.assertEqual(fit_inside(1024, 1024, 676, 950), (676, 676))
        self.assertEqual(feed_height(1024, 1024), 676)

    def test_photos_read_at_original_resolution(self):
        img = Image.new('RGB', (2560, 1440), '#123456')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        self.assertEqual(image_size(buf.getvalue()), (2560, 1440))

    def test_invalid_dimensions(self):
        with self.assertRaises(ValueError):
            fit_inside(0, 720, 676, 500)
        with self.assertRaises(ValueError):
            feed_height(-5, 20)


if __name__ == '__main__':
    unittest.main()
