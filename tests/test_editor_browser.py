"""Editor save, timeline, and preview regressions. Project API calls are mocked.

Run with a healthy local server:
    py -3.12 tests/test_editor_browser.py
Set EASYEDIT_BROWSER_URL and EASYEDIT_BROWSER_JOB to use another server/project.
Requires the optional playwright package and installed Chrome.
Real source playback and HTTP persistence are covered by test_edit_workflow_browser.py.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import unittest
from urllib.request import urlopen
from urllib.parse import urlsplit

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

BASE = os.environ.get('EASYEDIT_BROWSER_URL', 'http://127.0.0.1:4331').rstrip('/')
JOB = os.environ.get('EASYEDIT_BROWSER_JOB', 'inception')


@unittest.skipIf(sync_playwright is None, 'Optional playwright package is not installed')
class EditorBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            with urlopen(f'{BASE}/api/edit/{JOB}', timeout=10) as response:
                cls.seed = json.load(response)
        except Exception as exc:
            raise unittest.SkipTest(f'Start easyedit and select a built fixture with EASYEDIT_BROWSER_JOB: {exc}')
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)
        cls.artifacts = Path(__file__).resolve().parents[1] / '.cache' / 'editor-check'
        cls.artifacts.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.state = copy.deepcopy(self.seed)
        self.state['seqSaved'] = False
        self.state['built']['exists'] = False
        self.state['built']['missing'] = False
        self.state['built']['video'] = None
        self.pending = []
        self.writes = []
        self.errors = []
        self.page = self.browser.new_page(viewport={'width': 1440, 'height': 950})
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('**/api/**', self.route)
        self.page.on('dialog', lambda dialog: dialog.accept())

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.page.close()

    def route(self, route):
        request = route.request
        path = urlsplit(request.url).path
        if request.method == 'POST':
            body = request.post_data_json
            self.writes.append((path, body))
            if path.endswith('/save'):
                self.pending.append((route, body))
            else:
                route.fulfill(status=409, json={'error': 'Browser fixture: rendering disabled'})
        elif path == f'/api/edit/{JOB}':
            route.fulfill(json=self.state)
        elif path.endswith('/wave'):
            route.fulfill(json={'rate': 100, 'peaks': []})
        elif path.endswith('/history'):
            route.fulfill(json={'versions': []})
        elif path.endswith('/media'):
            # The real live iframe still runs; these tests exercise its document/film bridge.
            # No project footage is decoded while checking save ordering and caption placement.
            sources = {(role, name) for role, names in self.state['sources'].items() for name in names}
            for kind in ('video', 'audio'):
                for track in (self.state.get('seq') or {}).get(kind, []):
                    for clip in track:
                        sources.add(tuple(clip['src'].split(':', 1)))
            route.fulfill(json={f'{role}:{name}': {'key': f'{role}:{name}', 'name': name, 'role': role,
                          'exists': True, 'duration': 120, 'width': 1920, 'height': 1080, 'fps': 60,
                          'hasAudio': role != 'montage', 'video': '', 'audio': ''} for role, name in sources})
        elif path.endswith('/clips'):
            route.fulfill(json={'clips': [], 'kept': [], 'sites': [{'id': 'youtube', 'name': 'YouTube'}],
                                'anime': None, 'free_gb': 30})
        elif path.endswith('/thumb'):
            route.fulfill(status=404, body='No thumbnail in this fixture')
        elif path.endswith('/source'):
            self.fail('Mocked editor tests must not stream existing project footage')
        else:
            route.fulfill(status=404, json={'error': f'Unmocked fixture endpoint: {path}'})

    def open(self):
        self.page.goto(f'{BASE}/editor/{JOB}', wait_until='domcontentloaded')
        self.page.wait_for_function('S !== null && doc !== null')

    def wait_saves(self, n):
        for _ in range(80):
            if len(self.pending) >= n:
                return
            self.page.wait_for_timeout(25)
        self.fail(f'Expected {n} queued save requests, saw {len(self.pending)}')

    def complete_save(self, index, error=None):
        route, body = self.pending[index]
        if error:
            route.fulfill(status=503, json={'error': error})
        else:
            for key in ('plan', 'quote', 'curation', 'settings'):
                if key in body:
                    self.state[key] = {**self.state[key], **copy.deepcopy(body[key])}
            if 'seq' in body:
                self.state['seqSaved'] = isinstance(body['seq'], dict)
                self.state['seq'] = copy.deepcopy(body['seq'])
            route.fulfill(json=self.state)

    def test_serialized_saves_and_apply_flush(self):
        self.open()
        self.page.evaluate("change(d => { d.plan.title = 'First change'; })")
        self.wait_saves(1)
        self.page.evaluate("change(d => { d.plan.title = 'Latest change'; }); void run('build', 'Cutting')")
        self.assertEqual(len(self.pending), 1)
        self.assertTrue(self.page.locator('#apply').is_disabled())
        self.complete_save(0)
        self.wait_saves(2)
        self.assertEqual(self.pending[0][1]['plan']['title'], 'First change')
        self.assertEqual(self.pending[1][1]['plan']['title'], 'Latest change')
        self.assertFalse(any(path.endswith('/run') for path, _ in self.writes))
        self.complete_save(1)
        self.page.wait_for_function('!saving && !starting')
        self.assertEqual(sum(path.endswith('/run') for path, _ in self.writes), 1)
        self.assertFalse(self.page.evaluate('dirty'))

    def test_save_failure_blocks_export_and_retry_keeps_changes(self):
        self.open()
        self.page.evaluate("change(d => { d.plan.title = 'Keep this edit'; }); void run('full', 'Exporting')")
        self.wait_saves(1)
        self.complete_save(0, 'Fixture save failed')
        self.page.wait_for_function('!saving && !starting')
        self.assertTrue(self.page.evaluate('dirty'))
        self.assertIn('Fixture save failed', self.page.locator('#chip').inner_text())
        self.assertFalse(any(path.endswith('/run') for path, _ in self.writes))
        self.page.screenshot(path=str(self.artifacts / 'save-error.png'))
        self.page.locator('#retrySave').click()
        self.wait_saves(2)
        self.assertEqual(self.pending[1][1]['plan']['title'], 'Keep this edit')
        self.complete_save(1)
        self.page.wait_for_function('!saving && !dirty')
        self.assertTrue(self.page.locator('#retrySave').is_hidden())
        self.assertFalse(self.page.locator('#apply').is_disabled())

    def test_arranged_sequence_is_editable_and_undo_restores_its_layout(self):
        self.state['seqSaved'] = True
        self.state['seq'] = {'v': 1, 'fps': 60, 'video': [[{'id': 'custom', 'src': 'montage:custom.mp4', 'kind': 'shot', 'at': 3, 'dur': 4, 'in': 0}]], 'audio': [], 'off': []}
        self.open()
        self.assertEqual(self.page.locator('#layers .ee-clip').count(), 1)
        self.assertFalse(self.page.locator('#splitBtn').is_hidden())
        self.assertTrue(self.page.locator('#beatLayout').is_visible())
        self.assertEqual(self.page.locator('#beatLayout').inner_text(), 'Auto cut to beat')
        self.page.locator('#layers .ee-clip[data-clip-id="custom"]').click()
        self.page.evaluate('seek(5)')
        self.page.locator('#splitBtn').click()
        self.wait_saves(1)
        clips = self.pending[0][1]['seq']['video'][0]
        self.assertEqual([(c['at'], c['dur'], c['in']) for c in clips], [(3, 2, 0), (5, 2, 2)])
        self.assertNotEqual(clips[0]['id'], clips[1]['id'])
        self.complete_save(0)
        self.page.wait_for_function('!saving && !dirty')
        self.assertTrue(self.page.evaluate('S.seqSaved'))
        self.page.locator('#undo').click()
        self.wait_saves(2)
        restored = self.pending[1][1]['seq']['video'][0]
        self.assertEqual([(c['id'], c['at'], c['dur'], c['in']) for c in restored], [('custom', 3, 4, 0)])
        self.complete_save(1)
        self.page.wait_for_function('!saving && !dirty')
        self.assertFalse(any(body.get('seq', 'absent') is None for _, body in self.writes))

    def test_sequence_preview_options_and_subtitle_positions(self):
        self.state['seqSaved'] = True
        self.state['seq'] = {'v': 1, 'fps': 60, 'video': [[{'id': 'arranged-clip', 'shot': 'bin-shot', 'src': 'montage:custom.mp4', 'kind': 'shot', 'at': 3, 'dur': 4, 'in': 0, 'opts': {'fx': ['glow'], 'tf': {'x': 120}}}]], 'audio': [], 'off': []}
        self.state['quote']['lines'] = [[{'text': 'Placed dialogue', 'start': 10, 'end': 10.75, 'role': None}]]
        speech = f"speech:{self.state['sources']['speech'][self.state['quote']['clip']]}"
        self.state['seq']['audio'] = [[{'id': 'dialogue', 'src': speech, 'kind': 'speech', 'at': 3.25, 'dur': 1.25, 'in': 10}]]
        self.state['sequenceCaptions'] = [{'start': 99, 'end': 100, 'words': [{'text': 'Old build', 't': 99}]}]
        self.open()
        self.page.wait_for_function('player !== null && !!player.setLive')
        film_shot = self.page.evaluate("$('frame').contentWindow.EDIT.shots.find(c => c.id === 'arranged-clip')")
        self.assertEqual((film_shot['fx'], film_shot['tf']), (['glow'], {'x': 120}))
        self.assertEqual(self.page.evaluate('EEPreview.captions()[0].start'), 3.25)
        self.page.evaluate("change(d => { d.seq.video[0][0].opts = {fx:['invert'], tf:{x:240}}; })")
        self.wait_saves(1)
        live_shot = self.page.evaluate("$('frame').contentWindow.EDIT.shots.find(c => c.id === 'arranged-clip')")
        self.assertEqual((live_shot['fx'], live_shot['tf']), (['invert'], {'x': 240}))
        self.assertEqual(self.page.evaluate("$('frame').contentWindow.EDIT.captions[0].start"), 3.25)
        self.complete_save(0)
        self.page.wait_for_function('!saving && !dirty')
        with self.page.expect_download() as download_info:
            self.page.evaluate("$('srt').click()")
        download_path = self.artifacts / 'arranged-captions.srt'
        download_info.value.save_as(str(download_path))
        self.assertIn('00:00:03,250 --> 00:00:04,500', download_path.read_text(encoding='utf-8'))

    def test_narrow_panels_controls_and_time_carry(self):
        self.open()
        self.assertEqual(self.page.evaluate('clock(59.999)'), '1:00.00')
        for width in (320, 440, 768, 1024, 1440):
            self.page.set_viewport_size({'width': width, 'height': 850})
            self.page.wait_for_timeout(150)
            self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width, f'Overflow at {width}px')
            self.assertTrue(self.page.locator('#apply').is_visible())
            self.assertTrue(self.page.locator('#export').is_visible())
            if width <= 1000:
                for panel, selector in [('project', '#left'), ('controls', '#right'), ('viewer', '#mon')]:
                    self.page.locator(f'#panelnav [data-panel={panel}]').click()
                    self.assertTrue(self.page.locator(selector).is_visible())
                    self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
            self.page.screenshot(path=str(self.artifacts / f'editor-{width}.png'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
