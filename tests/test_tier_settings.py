"""Tier renames replace their starter without changing bundled files."""
import asyncio
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main, services
from font_fixtures import sample_font
from test_upload_security import asgi_request, image_bytes


class TierSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'data'
        data = patch.object(services, 'DATA_ROOT', self.root)
        data.start()
        self.addCleanup(data.stop)

    def names(self):
        return [item['name'] for item in main.templates()]

    def assert_renamed(self, name):
        self.assertEqual(self.names(), [name, 'Sponsors', 'Super Sponsors'])
        installed = services.discover_templates()
        self.assertIsNone(services.template_for_tier('Attendees', installed))
        self.assertIsNotNone(services.template_for_tier(name, installed))

    def test_rename_default_through_api_survives_reload_without_changing_builtin(self):
        starter = services.discover_templates()['attendees']['root'] / 'template.json'
        original = starter.read_bytes()
        status, _, response = asyncio.run(asgi_request('/api/templates/attendees/settings',
            json.dumps({'name': 'Attendees-A', 'profile_picture': False}).encode(), method='PUT',
            headers=[(b'content-type', b'application/json')]))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response)['id'], 'tier-attendees-a')
        self.assert_renamed('Attendees-A')
        status, _, response = asyncio.run(asgi_request('/api/templates', method='GET'))
        self.assertEqual(status, 200)
        self.assertEqual([item['name'] for item in json.loads(response)], self.names())
        self.assertEqual(starter.read_bytes(), original)
        self.assertIn('attendees', services.discover_templates())  # Existing record ID remains resolvable.

    def test_repeated_renames_and_all_edit_paths_preserve_starter_association(self):
        result = services.update_template_settings('attendees', 'Attendees-A', False)
        identifier = result['id']
        initial = copy.deepcopy(services.discover_templates()[identifier]['manifest']['elements'])
        services.save_template_layout(identifier, initial)
        services.add_template_asset(identifier, 'artwork', 'art.png', image_bytes())
        services.add_template_asset(identifier, 'mask', 'mask.png', image_bytes())
        font = io.BytesIO()
        sample_font('RenameTest').save(font)
        services.add_template_font(identifier, 'RenameTest', 'regular', 'font.ttf', font.getvalue())
        services.update_template_background(identifier, 'background.png', image_bytes(size=(120, 160)))
        self.assert_renamed('Attendees-A')
        before = services.discover_templates()[identifier]
        assets = {path.relative_to(before['root']): path.read_bytes() for path in before['root'].rglob('*')
                  if path.is_file() and path.name != 'template.json'}
        elements = copy.deepcopy(before['manifest']['elements'])
        result = services.update_template_settings(identifier, 'Attendees-B', False)
        self.assert_renamed('Attendees-B')
        saved = services.discover_templates()[result['id']]
        self.assertEqual(saved['manifest']['builtin_source'], 'attendees')
        self.assertEqual(saved['manifest']['elements'], elements)
        self.assertFalse((self.root / 'templates' / identifier).exists())
        for relative, content in assets.items():
            self.assertEqual((saved['root'] / relative).read_bytes(), content)

    def test_editing_before_rename_records_the_builtin_source(self):
        for edit in (lambda: services.save_template_layout('attendees', []),
                     lambda: services.add_template_asset('attendees', 'artwork', 'art.png', image_bytes()),
                     lambda: services.update_template_background('attendees', 'bg.png', image_bytes(size=(120, 160)))):
            with tempfile.TemporaryDirectory() as temporary, patch.object(services, 'DATA_ROOT', Path(temporary)):
                result = edit()
                self.assertEqual(services.discover_templates()[result['id']]['manifest']['builtin_source'], 'attendees')
                services.update_template_settings(result['id'], 'Attendees-A', False)
                self.assert_renamed('Attendees-A')

    def test_legacy_same_name_override_can_be_renamed(self):
        manifest = copy.deepcopy(services.discover_templates()['attendees']['manifest'])
        manifest.update(id='tier-attendees', priority=100)
        services.write_json(self.root / 'templates/tier-attendees/template.json', manifest)
        services.update_template_settings('tier-attendees', 'Attendees-A', False)
        self.assert_renamed('Attendees-A')

    def test_custom_tier_rename_does_not_hide_unrelated_starters(self):
        created = services.create_tier_template('VIP', 'bg.png', image_bytes(size=(120, 160)), False)
        renamed = services.update_template_settings(created['id'], 'VIP-A', False)
        self.assertEqual(self.names(), ['Attendees', 'Sponsors', 'Super Sponsors', 'VIP-A'])
        self.assertNotIn('builtin_source', services.discover_templates()[renamed['id']]['manifest'])

    def test_format_export_import_retains_rename_in_fresh_data_directory(self):
        services.update_template_settings('attendees', 'Attendees-A', False)
        payload = services.template_set_json()
        package = json.loads(payload)
        self.assertEqual([item['manifest']['name'] for item in package['templates']], self.names())
        renamed = next(item for item in package['templates'] if item['manifest']['name'] == 'Attendees-A')
        self.assertEqual(renamed['manifest']['builtin_source'], 'attendees')
        with patch.object(services, 'DATA_ROOT', self.root.parent / 'restored'):
            services.import_template_set_json(payload)
            self.assert_renamed('Attendees-A')
            services.update_template_settings('tier-attendees-a', 'Attendees-B', False)
            self.assert_renamed('Attendees-B')

    def test_deleting_replacement_restores_its_starter(self):
        renamed = services.update_template_settings('attendees', 'Attendees-A', False)
        services.delete_custom_template(renamed['id'])
        self.assertEqual(self.names(), ['Attendees', 'Sponsors', 'Super Sponsors'])

    def test_rename_collision_leaves_existing_tiers_intact(self):
        created = services.create_tier_template('VIP', 'bg.png', image_bytes(size=(120, 160)), False)
        before = self.names()
        for name in ('Sponsors', 'sPoNsOrS', 'VIP'):
            with self.subTest(name=name), self.assertRaises(services.BadgeError):
                services.update_template_settings('attendees', name, False)
            self.assertEqual(self.names(), before)
            self.assertIn(created['id'], services.discover_templates())

    def test_invalid_builtin_source_in_import_is_rejected(self):
        package = json.loads(services.template_set_json())
        for source in ([], {}, False, None, '', 'a' * 121):
            package['templates'][0]['manifest']['builtin_source'] = source
            with self.subTest(source=source), self.assertRaises(services.BadgeError):
                services.import_template_set_json(json.dumps(package).encode())
        self.assertEqual(self.names(), ['Attendees', 'Sponsors', 'Super Sponsors'])


if __name__ == '__main__':
    unittest.main()
