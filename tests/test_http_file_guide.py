"""Read-only explanations work even when source-body preview is forbidden."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

from atlas import serve
from atlas.asset_service import AssetService


class FileGuideHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.home() / '.hermes/cache/scratch')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / 'note.md'
        self.path.write_text('# Example\nOriginal text.\n')
        self.service = AssetService(self.root, lambda: {'files': []})
        self.service.catalog['files'] = [
            {'path': str(self.path), 'category': 'memory', 'platform': 'other', 'searchable': True, 'editable': False},
            {'path': str(self.root / 'state.sqlite'), 'category': 'session', 'platform': 'hermes', 'searchable': False, 'editable': False},
        ]
        self.server = serve.ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
        self.server.assets = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = 'http://127.0.0.1:%s' % self.server.server_port

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def call(self, path, headers=None):
        try:
            with urlopen(Request(self.url + path, headers=headers or {}), timeout=4) as reply:
                return reply.status, json.load(reply)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_file_guide_is_metadata_only_and_catalog_bounded(self):
        before = self.path.read_bytes()
        with patch.object(self.service.file_store, 'read', side_effect=AssertionError('must not preview')):
            status, book = self.call('/api/file-guide')
            self.assertEqual(status, 200)
            self.assertTrue(book['data']['types'])
            database = str(self.root / 'state.sqlite')
            status, description = self.call('/api/file-guide?' + urlencode({'path': database}))
            self.assertEqual(status, 200)
            self.assertEqual(description['data']['id'], 'category.session')
            self.assertEqual(description['data']['path'], database)
            self.assertNotIn('content', description['data'])
            self.assertNotIn('editable', description['data'])
            status, _ = self.call('/api/file-guide?' + urlencode({'path': str(self.root / 'not-indexed.md')}))
            self.assertEqual(status, 403)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.call('/api/file?' + urlencode({'path': database}))[0], 403)
        self.assertEqual(self.call('/api/file-guide?path=')[0], 400)
        self.assertEqual(self.call('/api/file-guide?path=x&path=y')[0], 400)
        self.assertEqual(self.call('/api/file-guide?unexpected=yes')[0], 400)
        self.assertEqual(self.call('/api/file-guide', {'Origin': 'https://foreign.example'})[0], 403)

    def test_preview_includes_guide_and_survives_a_broken_document(self):
        route = '/api/file?' + urlencode({'path': str(self.path)})
        status, result = self.call(route)
        self.assertEqual(status, 200)
        self.assertEqual(result['data']['fileGuide']['id'], 'category.memory')
        self.assertFalse(result['data']['editable'])
        broken = self.root / 'broken-guide.json'
        broken.write_text('{')
        self.service.file_guide.path = broken
        self.assertEqual(self.call('/api/file-guide')[0], 503)
        status, result = self.call(route)
        self.assertEqual(status, 200)
        self.assertIn('Original text.', result['data']['content'])
        self.assertIn('fileGuideError', result['data'])
        self.assertFalse(result['data']['editable'])


if __name__ == '__main__':
    unittest.main()
