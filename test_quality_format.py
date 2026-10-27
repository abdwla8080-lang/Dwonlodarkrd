import unittest

from app import quality_format


class QualityFormatTests(unittest.TestCase):
    def test_1080p_uses_fallback_video_plus_audio_chain(self):
        fmt = quality_format('1080p')
        self.assertIn('bestvideo[height<=1080]', fmt)
        self.assertIn('+bestaudio', fmt)
        self.assertIn('/best', fmt)
        self.assertNotIn('bv*', fmt)

    def test_best_uses_generic_bestvideo_fallback(self):
        fmt = quality_format('best')
        self.assertIn('bestvideo+bestaudio', fmt)
        self.assertIn('/best', fmt)


if __name__ == '__main__':
    unittest.main()
