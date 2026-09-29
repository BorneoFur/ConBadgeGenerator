"""Offline tests for official downloads, fallback coverage, and portable licenses."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import google_fonts, services, typography
from font_fixtures import sample_font


class GoogleFontTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        stream = io.BytesIO()
        sample_font('Example', characters='A é字简繁한あ').save(stream)
        self.content = stream.getvalue()
        self.license = b'Copyright Example\nSIL OPEN FONT LICENSE Version 1.1'
        self.metadata = b'''name: "Example"
license: "OFL"
fonts {
  style: "normal"
  weight: 400
  filename: "Example-Regular.ttf"
}
fonts {
  style: "normal"
  weight: 700
  filename: "Example-Bold.ttf"
}
'''

    def download(self, relative):
        if relative.endswith('METADATA.pb'):
            return self.metadata
        if relative.endswith('OFL.txt'):
            return self.license
        return self.content

    def test_official_link_download_cache_and_style(self):
        with patch.object(google_fonts, 'download', side_effect=self.download) as fetch:
            info = google_fonts.ensure_font('https://fonts.google.com/specimen/Example', self.root)
            self.assertEqual(info['path'].read_bytes(), self.content)
            self.assertEqual(info['license_path'].read_bytes(), self.license)
            google_fonts.ensure_font('Example', self.root)
            self.assertEqual(fetch.call_count, 3)
            google_fonts.ensure_font('Example', self.root, bold=True)
            self.assertIn(('example/Example-Bold.ttf',), [call.args for call in fetch.call_args_list])
            with self.assertRaisesRegex(google_fonts.FontError, 'requested style'):
                google_fonts.ensure_font('Example', self.root, italic=True)

    def test_reject_untrusted_links_and_license(self):
        for url in ('https://example.org/font.ttf', 'https://fonts.google.com.evil/specimen/A', '../font', 'file:///font'):
            with self.subTest(url=url), self.assertRaises(google_fonts.FontError):
                google_fonts.family_id(url)
        with patch.object(google_fonts, 'download', return_value=b'name: "Example"\nlicense: "OTHER"'):
            with self.assertRaisesRegex(google_fonts.FontError, 'OFL'):
                google_fonts.ensure_font('Example', self.root)

    def test_template_roundtrip_preserves_font_license_and_upload_origin(self):
        with patch.object(services, 'DATA_ROOT', self.root / 'data'), patch.object(google_fonts, 'download', side_effect=self.download):
            result = services.add_google_font('attendees', 'Example')
            font = result['font']
            package = services.template_set_json()
        with patch.object(services, 'DATA_ROOT', self.root / 'restored'):
            services.import_template_set_json(package)
            template = services.discover_templates()[result['id']]
            origin = template['manifest']['fonts'][0]['origins']['regular']
            self.assertEqual(origin['license'], 'OFL-1.1')
            self.assertEqual((template['root'] / origin['license_asset']).read_bytes(), self.license)
            self.assertEqual((template['root'] / font['regular']).read_bytes(), self.content)
            upload = services.add_template_font(result['id'], 'Example', 'bold', 'own.ttf', self.content)
            self.assertEqual(upload['font']['origins']['bold'], {'source': 'upload'})
            self.assertEqual(upload['font']['origins']['regular']['source'], 'google-fonts')

    def test_multilingual_fallback_and_missing_glyph_error(self):
        paths = {}
        for identifier, chars in {'notosans': 'Aé', 'notosanssc': '简', 'notosanstc': '繁', 'notosansjp': 'あ', 'notosanskr': '한'}.items():
            path = self.root / f'{identifier}.ttf'
            sample_font(identifier, characters=chars).save(path)
            paths[identifier] = path
        def cached(identifier, cache):
            return {'path': paths[identifier]}
        with patch.object(google_fonts, 'ensure_font', side_effect=cached):
            resolver = typography.FontResolver(None, self.root, '简繁あ한é')
            for char, identifier in [('简', 'notosanssc'), ('繁', 'notosanstc'), ('あ', 'notosansjp'), ('한', 'notosanskr'), ('é', 'notosans')]:
                self.assertEqual(resolver.choose(char), paths[identifier])
            with self.assertRaisesRegex(google_fonts.FontError, 'U\\+1F984'):
                resolver.choose('🦄')
            preferred = typography.FontResolver(paths['notosanstc'], self.root, '繁')
            self.assertEqual(preferred.choose('繁'), paths['notosanstc'])

    def test_combining_accents_are_one_grapheme_and_match_composed_text(self):
        path = self.root / 'font.ttf'
        path.write_bytes(self.content)
        element = {'_ppi': 72, 'width_mm': 60, 'height_mm': 20, 'font_size_pt': 20}
        composed = typography.render_text(element, 'é', path, self.root)
        decomposed = typography.render_text(element, 'e\u0301', path, self.root)
        self.assertEqual(composed.tobytes(), decomposed.tobytes())
        self.assertEqual(typography.graphemes('e\u0323\u0301'), ['ẹ\u0301'])
        self.assertIsNotNone(composed.getbbox())


if __name__ == '__main__':
    unittest.main()
