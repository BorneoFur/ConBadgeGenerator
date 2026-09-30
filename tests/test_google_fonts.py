"""Offline tests for official downloads, fallback coverage, and portable licenses."""
import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app import google_fonts, services, typography
from font_fixtures import sample_font
from test_upload_security import asgi_request


class GoogleFontTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        stream = io.BytesIO()
        sample_font('Example', characters='A é字简繁한あ').save(stream)
        self.content = stream.getvalue()
        self.license = b'Copyright Example\nSIL OPEN FONT LICENSE Version 1.1'
        self.apache_license = b'Apache License\nVersion 2.0, January 2004'
        self.ufl_license = b'UBUNTU FONT LICENCE Version 1.0'
        self.ubuntu_styles = {'regular': 'Regular', 'bold': 'Bold', 'italic': 'Italic', 'bold_italic': 'BoldItalic'}
        self.ubuntu_metadata = ('name: "Ubuntu"\nlicense: "UFL"\n' + ''.join(
            'fonts {\n'
            f'  style: "{"italic" if "italic" in style else "normal"}"\n'
            f'  weight: {700 if "bold" in style else 400}\n'
            f'  filename: "Ubuntu-{suffix}.ttf"\n'
            '}\n' for style, suffix in self.ubuntu_styles.items()
        )).encode()
        self.chewy_metadata = b'''name: "Chewy"
license: "APACHE2"
fonts {
  style: "normal"
  weight: 400
  filename: "Chewy-Regular.ttf"
}
'''
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

    def open_chewy(self, request, timeout):
        files = {
            'apache/chewy/METADATA.pb': self.chewy_metadata,
            'apache/chewy/Chewy-Regular.ttf': self.content,
            'apache/chewy/LICENSE.txt': self.apache_license,
        }
        relative = request.full_url.removeprefix('https://raw.githubusercontent.com/google/fonts/main/')
        if relative not in files:
            raise HTTPError(request.full_url, 404, 'Not Found', {}, None)
        return io.BytesIO(files[relative])

    def open_ubuntu(self, request, timeout):
        files = {
            'ufl/ubuntu/METADATA.pb': self.ubuntu_metadata,
            'ufl/ubuntu/UFL.txt': self.ufl_license,
            **{f'ufl/ubuntu/Ubuntu-{suffix}.ttf': self.content for suffix in self.ubuntu_styles.values()},
        }
        relative = request.full_url.removeprefix('https://raw.githubusercontent.com/google/fonts/main/')
        if relative not in files:
            raise HTTPError(request.full_url, 404, 'Not Found', {}, None)
        return io.BytesIO(files[relative])

    def test_ubuntu_link_styles_cache_and_portable_ufl_license(self):
        with patch.object(services, 'DATA_ROOT', self.root / 'data'), patch.object(google_fonts, 'urlopen', side_effect=self.open_ubuntu) as fetch:
            template_id = 'attendees'
            for style, suffix in self.ubuntu_styles.items():
                with self.subTest(style=style):
                    fetch.reset_mock()
                    status, _, response = asyncio.run(asgi_request(f'/api/templates/{template_id}/google-fonts',
                        json.dumps({'font_id': 'https://fonts.google.com/specimen/Ubuntu', 'style': style}).encode(),
                        headers=[(b'content-type', b'application/json')]))
                    self.assertEqual(status, 200, response)
                    result = json.loads(response)
                    template_id = result['id']
                    self.assertEqual(result['font']['name'], 'Ubuntu')
                    self.assertEqual(fetch.call_count, 5)  # Two 404s, UFL metadata, font, license.
                    origin = result['font']['origins'][style]
                    self.assertEqual(origin['download_url'], f'https://raw.githubusercontent.com/google/fonts/main/ufl/ubuntu/Ubuntu-{suffix}.ttf')
                    fetch.reset_mock()
                    info = google_fonts.ensure_font('Ubuntu', services.DATA_ROOT / 'google-fonts',
                                                    italic='italic' in style, bold='bold' in style)
                    fetch.assert_not_called()
                    self.assertEqual(info['license'], 'Ubuntu-font-1.0')
                    self.assertEqual(info['license_path'].name, 'UFL.txt')
                    self.assertEqual(info['license_path'].read_bytes(), self.ufl_license)
            package = services.template_set_json()
        with patch.object(services, 'DATA_ROOT', self.root / 'restored'):
            services.import_template_set_json(package)
            template = services.discover_templates()[template_id]
            font = template['manifest']['fonts'][0]
            for style in self.ubuntu_styles:
                with self.subTest(restored_style=style):
                    origin = font['origins'][style]
                    self.assertEqual(origin['license'], 'Ubuntu-font-1.0')
                    self.assertTrue(origin['license_asset'].endswith('-UFL.txt'))
                    self.assertEqual((template['root'] / origin['license_asset']).read_bytes(), self.ufl_license)
                    self.assertEqual((template['root'] / font[style]).read_bytes(), self.content)

    def test_ufl_license_must_be_verified_before_caching(self):
        for content in (self.license, self.apache_license, b'UBUNTU FONT LICENCE without a version', b'not a license'):
            with self.subTest(content=content), patch.object(self, 'ufl_license', content), patch.object(google_fonts, 'urlopen', side_effect=self.open_ubuntu):
                with self.assertRaisesRegex(google_fonts.FontError, 'license could not be verified'):
                    google_fonts.ensure_font('Ubuntu', self.root)
                self.assertFalse((self.root / 'ubuntu' / 'regular.json').exists())

    def test_italic_only_family_default_style_api_cache_and_export(self):
        metadata = b'''name: "Molle"
license: "OFL"
fonts {
  style: "italic"
  weight: 400
  filename: "Molle-Regular.ttf"
}
'''
        with patch.object(services, 'DATA_ROOT', self.root / 'data'), patch.object(self, 'metadata', metadata), patch.object(google_fonts, 'download', side_effect=self.download) as fetch:
            template_id = 'attendees'
            # Repeat the default request to verify that the cached result also resolves to italic.
            for requested_style in (None, 'regular', 'italic'):
                payload = {'font_id': 'https://fonts.google.com/specimen/Molle'}
                if requested_style:
                    payload['style'] = requested_style
                fetch.reset_mock()
                status, _, response = asyncio.run(asgi_request(f'/api/templates/{template_id}/google-fonts',
                    json.dumps(payload).encode(), headers=[(b'content-type', b'application/json')]))
                self.assertEqual(status, 200, response)
                result = json.loads(response)
                template_id = result['id']
                self.assertEqual(result['style'], 'italic')
                self.assertNotIn('regular', result['font'])
                self.assertIn('italic', result['font'])
                origin = result['font']['origins']['italic']
                self.assertEqual(origin['style'], 'italic')
                self.assertTrue(origin['download_url'].endswith('/ofl/molle/Molle-Regular.ttf'))
                if requested_style == 'regular':
                    fetch.assert_not_called()
            template = services.discover_templates()[template_id]
            element = {'font_family': result['font']['id'], 'font_style': result['style']}
            selected = services._font_asset(element, template['root'])
            self.assertEqual(selected.read_bytes(), self.content)
            self.assertIsNotNone(typography.render_text({**element, '_ppi': 72, 'width_mm': 60,
                'height_mm': 20, 'font_size_pt': 20}, 'A', selected, services.DATA_ROOT / 'google-fonts').getbbox())
            package = services.template_set_json()
        with patch.object(services, 'DATA_ROOT', self.root / 'restored'):
            services.import_template_set_json(package)
            template = services.discover_templates()[template_id]
            font = template['manifest']['fonts'][0]
            self.assertEqual((template['root'] / font['italic']).read_bytes(), self.content)
            self.assertEqual((template['root'] / font['origins']['italic']['license_asset']).read_bytes(), self.license)

    def test_chewy_link_api_cache_and_portable_apache_license(self):
        with patch.object(services, 'DATA_ROOT', self.root / 'data'), patch.object(google_fonts, 'urlopen', side_effect=self.open_chewy) as fetch:
            status, _, response = asyncio.run(asgi_request('/api/templates/attendees/google-fonts',
                json.dumps({'font_id': 'https://fonts.google.com/specimen/Chewy\u00a0', 'style': 'regular'}).encode(),
                headers=[(b'content-type', b'application/json')]))
            self.assertEqual(status, 200, response)
            result = json.loads(response)
            self.assertEqual(result['font']['name'], 'Chewy')
            self.assertEqual(fetch.call_count, 4)  # OFL 404, Apache metadata, font, license.
            info = google_fonts.ensure_font('Chewy', services.DATA_ROOT / 'google-fonts')
            self.assertEqual(fetch.call_count, 4)
            self.assertEqual(info['license_path'].name, 'LICENSE.txt')
            self.assertEqual(info['license_path'].read_bytes(), self.apache_license)
            self.assertEqual(info['download_url'], 'https://raw.githubusercontent.com/google/fonts/main/apache/chewy/Chewy-Regular.ttf')
            package = services.template_set_json()
        with patch.object(services, 'DATA_ROOT', self.root / 'restored'):
            services.import_template_set_json(package)
            template = services.discover_templates()[result['id']]
            font = template['manifest']['fonts'][0]
            origin = font['origins']['regular']
            self.assertEqual(origin['license'], 'Apache-2.0')
            self.assertTrue(origin['license_asset'].endswith('-LICENSE.txt'))
            self.assertEqual((template['root'] / origin['license_asset']).read_bytes(), self.apache_license)
            self.assertEqual((template['root'] / font['regular']).read_bytes(), self.content)

    def test_apache_license_must_be_verified_before_caching(self):
        for content in (self.license, b'Apache License without a version', b'not a license'):
            with self.subTest(content=content), patch.object(self, 'apache_license', content), patch.object(google_fonts, 'urlopen', side_effect=self.open_chewy):
                with self.assertRaisesRegex(google_fonts.FontError, 'license could not be verified'):
                    google_fonts.ensure_font('Chewy', self.root)
                self.assertFalse((self.root / 'chewy' / 'regular.json').exists())

    def test_only_missing_metadata_tries_another_license_directory(self):
        for error in (HTTPError('https://raw.githubusercontent.com/', 403, 'Forbidden', {}, None),
                      HTTPError('https://raw.githubusercontent.com/', 500, 'Server Error', {}, None),
                      URLError('offline')):
            with self.subTest(error=error), patch.object(google_fonts, 'urlopen', side_effect=error) as fetch:
                with self.assertRaises(google_fonts.FontError):
                    google_fonts.ensure_font('Chewy', self.root)
                self.assertEqual(fetch.call_count, 1)

    def test_unknown_family_reports_supported_licenses(self):
        with patch.object(google_fonts, 'urlopen', side_effect=self.open_chewy) as fetch:
            with self.assertRaisesRegex(google_fonts.FontError, 'not found.*OFL.*Apache.*UFL'):
                google_fonts.ensure_font('UnknownFamily', self.root)
            self.assertEqual(fetch.call_count, 3)

    def test_official_link_download_cache_and_style(self):
        with patch.object(google_fonts, 'download', side_effect=self.download) as fetch:
            info = google_fonts.ensure_font('https://fonts.google.com/specimen/Example', self.root)
            self.assertEqual(info['style'], 'regular')
            self.assertEqual(info['path'].read_bytes(), self.content)
            self.assertEqual(info['license_path'].read_bytes(), self.license)
            google_fonts.ensure_font('Example', self.root)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(google_fonts.ensure_font('Example', self.root, bold=True)['style'], 'bold')
            self.assertIn(('ofl/example/Example-Bold.ttf',), [call.args for call in fetch.call_args_list])
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
