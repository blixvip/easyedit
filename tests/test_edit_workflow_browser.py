"""Real editor workflow: browser controls -> HTTP API -> saved sequence -> live video.

Opt in with EASYEDIT_REAL_BROWSER_TESTS=1 and run:
    py -3.12 -m pytest tests/test_edit_workflow_browser.py -v -s
The default server is http://127.0.0.1:4332 (EASYEDIT_BROWSER_URL overrides it).
Only a newly created ee-browser-* job is changed. Source patterns are tiny; no
AI, download, build, or export is run. Set EASYEDIT_KEEP_BROWSER_FIXTURE=1 to
leave that isolated job for a subsequent export check.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest
from urllib.request import urlopen
from uuid import uuid4

from PIL import Image

from easyedit import shots
from easyedit.util import JOBS, ROOT, write_json

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def prepare_fixture(job: Path) -> Path:
    """Two unmistakably different clips and cached analyses, without running models."""
    if job.exists():
        raise ValueError("the browser fixture must use a new directory")
    job = job.resolve()
    media = job / "downloads"
    media.mkdir(parents=True)
    red, blue, tone = media / "red.mp4", media / "blue.mp4", media / "tone.m4a"
    for target, color, frequency in ((red, "red", 330), (blue, "blue", 550)):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c={color}:size=320x180:rate=30", "-f", "lavfi", "-i",
                        f"sine=frequency={frequency}:sample_rate=48000", "-t", "4",
                        "-threads", "1", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(target)], check=True, timeout=30)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=48000", "-t", "4", "-threads", "1",
                    "-c:a", "aac", str(tone)], check=True, timeout=30)
    sources = {"specs": {"speech": None, "montage": None, "music": None},
               "speech": [str(red)], "montage": [str(red), str(blue)], "music": [str(tone)]}
    write_json(job / "sources.json", sources)
    write_json(job / "plan.json", {"title": "Real browser editing check", "year": 2026,
               "grade": "neutral", "palette": {"positive": "#66ddaa", "negative": "#ff6677",
               "gold": "#ffcc55", "cool": "#55aaff"}, "speech_query": "local fixture",
               "montage_queries": [], "music_query": "local fixture"})
    words = [{"text": "EDIT", "start": 0.15, "end": 0.35, "role": None},
             {"text": "CHECK", "start": 0.4, "end": 0.65, "role": None}]
    write_json(job / "quote.json", {"clip": 0, "files": [str(red)], "start": 0.15,
                                    "end": 0.65, "lines": [words]})
    write_json(job / "work/words-red.json", words)
    for index, source in enumerate((red, blue)):
        write_json(job / f"work/shots-{source.stem}.json", {"v": shots.CACHE_VERSION,
                   "source": str(source), "crop": None,
                   "shots": [{"start": 0, "end": 4, "face": None, "motion": 1 + index,
                              "sharp": 1, "contrast": 1, "sat": 50,
                              "thumb": [index] * (32 * 18)}]})
    # Avoid a crop probe for uniform test colors being mistaken for black bars.
    stat = red.stat()
    write_json(job / "work/crop-red.json", {"stamp": [round(stat.st_mtime, 3), stat.st_size], "crop": None})
    beats = [i / 2 for i in range(9)]
    write_json(job / "work/beats-tone.json", {"beats": beats, "bpm": 120,
               "loud": [1] * len(beats), "strength": [1] * len(beats), "duration": 4})
    write_json(job / "settings.json", {"fps": 30, "montage_length": 8, "hero_length": 2,
                                       "sfx": 0, "depth": False, "velocity": False})
    write_json(job / "timeline.json", {"v": 1, "fps": 30, "off": [], "video": [[
        {"id": "red", "src": "montage:red.mp4", "kind": "shot", "at": 0, "dur": 2,
         "in": 0, "shot": "red@0.00"},
        {"id": "blue", "src": "montage:blue.mp4", "kind": "shot", "at": 2, "dur": 2,
         "in": 0, "shot": "blue@0.00"}], []], "audio": [[
        {"id": "dialogue", "src": "speech:red.mp4", "kind": "speech", "at": 0,
         "dur": 1, "in": 0}], [
        {"id": "song", "src": "music:tone.m4a", "kind": "music", "at": 0,
         "dur": 4, "in": 0, "duck": True}]]})
    return job


@unittest.skipUnless(os.environ.get("EASYEDIT_REAL_BROWSER_TESTS") == "1",
                     "Real browser workflow is opt-in; set EASYEDIT_REAL_BROWSER_TESTS=1")
@unittest.skipIf(sync_playwright is None, "Optional playwright package is not installed")
class RealEditWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = os.environ.get("EASYEDIT_BROWSER_URL", "http://127.0.0.1:4332").rstrip("/")
        with urlopen(cls.base + "/api/jobs", timeout=10) as response:
            json.load(response)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)
        cls.addClassCleanup(cls.browser.close)

    def clean_fixture(self):
        if os.environ.get("EASYEDIT_KEEP_BROWSER_FIXTURE") == "1":
            return
        target = self.job.resolve()
        if target.parent != JOBS.resolve() or not target.name.startswith("ee-browser-"):
            raise AssertionError("refusing to remove a folder outside this test's fixture")
        shutil.rmtree(target)

    def setUp(self):
        self.job = JOBS / f"ee-browser-{uuid4().hex[:10]}"
        self.addCleanup(self.clean_fixture)
        prepare_fixture(self.job)
        self.artifacts = ROOT / ".cache" / "real-edit-check" / self.job.name
        self.artifacts.mkdir(parents=True, exist_ok=True)
        print(f"\nReal browser fixture: {self.job}\nScreenshots: {self.artifacts}")
        self.page = self.browser.new_page(viewport={"width": 1500, "height": 1000})
        self.addCleanup(self.page.close)
        self.errors = []
        self.failed_saves = []
        self.write_paths = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("request", self.observe_request)
        self.page.on("response", self.observe_response)
        self.page.on("dialog", lambda dialog: dialog.accept())

    def observe_request(self, request):
        if request.method == "POST":
            self.write_paths.append(request.url)

    def observe_response(self, response):
        if response.request.method == "POST" and response.url.endswith("/save") and response.status >= 400:
            self.failed_saves.append((response.status, response.text()))

    def disk_sequence(self):
        return json.loads((self.job / "timeline.json").read_text(encoding="utf-8"))

    def clip(self, clip_id):
        return self.page.locator(f'#layers .ee-clip[data-clip-id="{clip_id}"]')

    def save_action(self, action):
        with self.page.expect_response(lambda response: response.request.method == "POST"
                                      and response.url.endswith("/save"), timeout=15000) as result:
            action()
        self.assertEqual(result.value.status, 200, result.value.text())
        self.page.wait_for_function("!saving && !dirty && !saveError", timeout=15000)
        return self.disk_sequence()

    def seek(self, seconds):
        pps = self.page.evaluate("pps")
        self.page.locator("#ruler").click(position={"x": seconds * pps, "y": 8})
        self.page.wait_for_function("t => Math.abs(now - t) < 0.08", arg=seconds)

    def preview_color(self, expected):
        """Read actual decoded video pixels and inspect the composed monitor screenshot."""
        frame = self.page.frame_locator("#frame")
        canvas = frame.locator("#footage")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            pixel = canvas.evaluate("c => Array.from(c.getContext('2d').getImageData(100, 100, 1, 1).data)")
            dominant = 0 if expected == "red" else 2
            other = 2 if expected == "red" else 0
            if pixel[dominant] > 120 and pixel[dominant] > pixel[other] + 80:
                break
            self.page.wait_for_timeout(80)
        else:
            self.fail(f"Live video should be {expected}; decoded pixel was {pixel}")
        image = Image.open(io.BytesIO(self.page.locator("#stagewrap").screenshot())).convert("RGB")
        sample = image.getpixel((int(image.width * 0.25), int(image.height * 0.35)))
        self.assertGreater(sample[dominant], sample[other] + 40,
                           f"Visible monitor should show {expected}, got {sample}")

    def inspector_number(self, name, value):
        field = self.page.locator(f'#seqInspector [data-seq-field="{name}"]')
        field.fill(str(value))
        return self.save_action(lambda: field.press("Enter"))

    def monitor_pixel(self):
        image = Image.open(io.BytesIO(self.page.locator("#stagewrap").screenshot())).convert("RGB")
        return image.getpixel((int(image.width * 0.25), int(image.height * 0.35)))

    def test_real_edit_save_undo_reload_and_live_source_playback(self):
        self.page.goto(f"{self.base}/editor/{self.job.name}", wait_until="domcontentloaded")
        self.clip("blue").wait_for(timeout=15000)
        self.page.wait_for_function("!!player && !!doc.seq", timeout=15000)
        self.assertFalse((self.job / "render/footage.mp4").exists())
        self.seek(1)
        self.preview_color("red")
        self.page.screenshot(path=str(self.artifacts / "01-before.png"))

        # Drag an actual clip between video tracks and earlier in time.
        blue = self.clip("blue").bounding_box()
        track = self.page.locator('.ee-track[data-kind="video"][data-track-index="1"] .lbars').bounding_box()
        pps = self.page.evaluate("pps")
        grab = blue["width"] / 2
        self.page.mouse.move(blue["x"] + grab, blue["y"] + blue["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(track["x"] + 0.5 * pps + grab, track["y"] + track["height"] / 2, steps=12)
        seq = self.save_action(self.page.mouse.up)
        moved = next(c for c in seq["video"][1] if c["id"] == "blue")
        self.assertAlmostEqual(moved["at"], 0.5, delta=1 / 30)
        self.assertEqual([c["id"] for c in seq["video"][0]], ["red"])
        self.seek(1)
        self.preview_color("blue")
        self.page.screenshot(path=str(self.artifacts / "02-moved-live.png"))

        seq = self.save_action(lambda: self.page.locator("#undo").click())
        self.assertEqual([c["id"] for c in seq["video"][0]], ["red", "blue"])
        self.assertEqual(seq["video"][1], [])
        self.preview_color("red")
        seq = self.save_action(lambda: self.page.locator("#redo").click())
        self.assertEqual(seq["video"][1][0]["id"], "blue")
        self.preview_color("blue")

        # The real trim handle must change both the visible clip and persisted duration.
        handle = self.clip("blue").locator('[data-trim="end"]').bounding_box()
        self.page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(handle["x"] + handle["width"] / 2 - 0.5 * pps,
                             handle["y"] + handle["height"] / 2, steps=10)
        seq = self.save_action(self.page.mouse.up)
        self.assertAlmostEqual(seq["video"][1][0]["dur"], 1.5, delta=1 / 30)

        self.clip("blue").click()
        self.seek(1.25)
        seq = self.save_action(lambda: self.page.locator("#splitBtn").click())
        pieces = sorted(seq["video"][1], key=lambda c: c["at"])
        self.assertEqual(len(pieces), 2)
        self.assertAlmostEqual(sum(c["dur"] for c in pieces), 1.5, delta=1 / 30)
        self.assertAlmostEqual(pieces[1]["in"], pieces[0]["in"] + pieces[0]["dur"], delta=1 / 30)

        self.clip(pieces[1]["id"]).click()
        seq = self.save_action(lambda: self.page.locator("#seqDelete").click())
        self.assertEqual(len(seq["video"][1]), 1)
        self.assertEqual(seq["video"][0][0]["id"], "red")

        self.clip("blue").click()
        seq = self.save_action(lambda: self.page.locator("#seqDuplicate").click())
        self.assertEqual(len(seq["video"][1]), 2)
        self.assertEqual(len({c["id"] for c in seq["video"][1]}), 2)
        seq = self.save_action(lambda: self.page.locator("#undo").click())
        self.assertEqual(len(seq["video"][1]), 1)

        mute = self.page.locator('[data-track-toggle][data-kind="audio"][data-track-index="1"]')
        seq = self.save_action(mute.click)
        self.assertIn("A2", seq["off"])
        self.assertNotIn("song", self.page.evaluate("easyeditLive().sounds.map(s => s.id)"))

        self.page.reload(wait_until="domcontentloaded")
        self.clip("blue").wait_for()
        self.page.wait_for_function("!!player && !!doc.seq")
        self.assertEqual(self.page.evaluate("doc.seq"), self.disk_sequence())
        self.seek(0.9)
        self.preview_color("blue")
        self.assertIn("A2", self.page.evaluate("doc.seq.off"))
        self.page.screenshot(path=str(self.artifacts / "03-reloaded.png"))
        self.page.set_viewport_size({"width": 440, "height": 900})
        self.page.wait_for_timeout(150)
        self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), 440)
        self.assertTrue(self.page.locator("#apply").is_visible())
        self.page.screenshot(path=str(self.artifacts / "04-narrow.png"))
        self.assertFalse((self.job / "render/footage.mp4").exists(), "Editing must not require a rendered cut")
        self.assertTrue(self.write_paths)
        self.assertTrue(all(url.endswith("/save") for url in self.write_paths), self.write_paths)
        self.assertEqual(self.failed_saves, [])
        self.assertEqual(self.errors, [])

    def test_extended_bin_inspector_effects_captions_and_linked_footage(self):
        self.page.goto(f"{self.base}/editor/{self.job.name}", wait_until="domcontentloaded")
        self.clip("red").wait_for(timeout=15000)
        self.page.wait_for_function("!!player && !!doc.seq && !!L", timeout=15000)

        # Inspector inputs update actual clip timing and playback speed.
        self.clip("red").click()
        self.inspector_number("at", 0.5)
        self.inspector_number("dur", 1.5)
        self.inspector_number("in", 0.25)
        seq = self.inspector_number("speed", 1.25)
        red = next(c for c in seq["video"][0] if c["id"] == "red")
        self.assertEqual((red["at"], red["dur"], red["in"], red["speed"]), (0.5, 1.5, 0.25, 1.25))
        self.seek(1)
        self.preview_color("red")

        # Mono must be visible in the composed monitor, not just stored in a checkbox.
        effects = self.page.locator("#seqInspector details").filter(
            has=self.page.locator("summary", has_text="Effects"))
        effects.locator("summary").click()
        seq = self.save_action(lambda: effects.locator('[data-seq-fx="mono"]').check())
        self.assertIn("mono", next(c for c in seq["video"][0] if c["id"] == "red")["opts"]["fx"])
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            pixel = self.monitor_pixel()
            if max(pixel) - min(pixel) < 25 and max(pixel) > 15:
                break
            self.page.wait_for_timeout(80)
        else:
            self.fail(f"Mono effect did not reach the visible preview: {pixel}")
        self.page.screenshot(path=str(self.artifacts / "05-mono-effect.png"))

        self.clip("song").click()
        seq = self.inspector_number("gain", 0.35)
        song = next(c for track in seq["audio"] for c in track if c["id"] == "song")
        self.assertEqual(song["gain"], 0.35)
        self.assertEqual(next(c["gain"] for c in self.page.evaluate("easyeditLive().sounds")
                              if c["id"] == "song"), 0.35)

        # Drag a Project bin shot into an empty video track, then replace another clip by dropping on it.
        self.page.locator('#ltabs [data-lt="project"]').click()
        blue_shot = self.page.locator('#shots .shot[data-id="blue@0.00"]')
        target = self.page.locator('.ee-track[data-kind="video"][data-track-index="1"] .lbars')
        pps = self.page.evaluate("pps")
        seq = self.save_action(lambda: blue_shot.drag_to(target, target_position={"x": pps * 0.5, "y": 20}))
        self.assertEqual(len(seq["video"][1]), 1)
        added = seq["video"][1][0]
        self.assertEqual(added["src"], "montage:blue.mp4")
        self.assertAlmostEqual(added["at"], 0.5, delta=1 / 30)
        self.seek(1)
        self.preview_color("blue")
        self.page.locator('#ltabs [data-lt="project"]').click()
        seq = self.save_action(lambda: blue_shot.drag_to(self.clip("red")))
        replaced = next(c for c in seq["video"][0] if c["id"] == "red")
        self.assertEqual(replaced["src"], "montage:blue.mp4")
        self.assertEqual((replaced["at"], replaced["dur"]), (0.5, 1.5))
        self.assertEqual(sum(map(len, seq["video"])), 3)

        # Caption text changes pass through the real word control and survive a reload.
        self.page.locator('#tabs [data-tab="captions"]').click()
        self.page.locator('#caps [data-l="0"][data-w="0"]').click()
        self.save_action(lambda: self.page.locator("#wtext").fill("Edited"))
        quote = json.loads((self.job / "quote.json").read_text(encoding="utf-8"))
        self.assertEqual(quote["lines"][0][0]["text"], "EDITED")
        self.assertIn("EDITED", [w["text"] for line in self.page.evaluate("easyeditLive().edit.captions")
                                 for w in line["words"]])

        # Add a complete source through Footage; its audio and video must remain linked when moved.
        self.seek(3)
        self.page.locator('#ltabs [data-lt="clips"]').click()
        source = self.page.locator("#lib .src").filter(has=self.page.locator('b[title="blue"]'))
        before_ids = {c["id"] for tracks in (seq["video"], seq["audio"]) for track in tracks for c in track}
        seq = self.save_action(lambda: source.locator("[data-insert]").click())
        added_video = [c for track in seq["video"] for c in track if c["id"] not in before_ids]
        added_audio = [c for track in seq["audio"] for c in track if c["id"] not in before_ids]
        self.assertEqual((len(added_video), len(added_audio)), (1, 1))
        video, audio = added_video[0], added_audio[0]
        self.assertEqual((video["src"], audio["src"]), ("montage:blue.mp4", "montage:blue.mp4"))
        self.assertEqual((video["link"], audio["link"]), (audio["id"], video["id"]))
        self.assertEqual((video["at"], audio["at"]), (3, 3))
        self.clip(video["id"]).click()
        seq = self.inspector_number("at", 2.5)
        linked = [c for tracks in (seq["video"], seq["audio"]) for track in tracks for c in track
                  if c["id"] in (video["id"], audio["id"])]
        self.assertEqual([c["at"] for c in linked], [2.5, 2.5])
        self.page.screenshot(path=str(self.artifacts / "06-linked-footage.png"))

        self.page.reload(wait_until="domcontentloaded")
        self.clip(video["id"]).wait_for()
        self.page.wait_for_function("!!player && !!doc.seq")
        self.assertEqual(self.page.evaluate("doc.seq"), self.disk_sequence())
        self.assertEqual(self.page.locator('#caps [data-l="0"][data-w="0"]').inner_text(), "EDITED")
        self.assertFalse((self.job / "render/footage.mp4").exists())
        self.assertEqual(self.failed_saves, [])
        self.assertEqual(self.errors, [])

    def test_razor_hand_and_zoom_tools(self):
        self.page.goto(f"{self.base}/editor/{self.job.name}", wait_until="domcontentloaded")
        self.clip("red").wait_for(timeout=15000)
        self.page.wait_for_function("!!player && !!doc.seq", timeout=15000)
        self.page.locator('#nleTools [data-t="cut"]').click()
        self.assertEqual(self.page.locator("body").get_attribute("data-tool"), "cut")
        seq = self.save_action(lambda: self.clip("red").click())
        red_pieces = [c for c in seq["video"][0] if c["src"] == "montage:red.mp4"]
        self.assertEqual(len(red_pieces), 2)
        self.assertAlmostEqual(red_pieces[0]["dur"], 1, delta=1 / 30)
        self.assertAlmostEqual(red_pieces[1]["in"], red_pieces[0]["dur"], delta=1 / 30)
        saved = (self.job / "timeline.json").read_bytes()
        saved_requests = len(self.write_paths)

        # Timeline zoom changes its scale, without adding a save or cutting a clip.
        self.page.locator('#nleTools [data-t="zoom"]').click()
        before = self.page.evaluate("pps")
        self.clip("red").click()
        zoomed = self.page.evaluate("pps")
        self.assertGreater(zoomed, before)
        self.assertEqual(self.page.locator("body").get_attribute("data-tool"), "zoom")

        self.page.locator('#nleTools [data-t="hand"]').click()
        scroller = self.page.locator("#tlscroll").bounding_box()
        row = self.page.locator('.ee-track[data-kind="video"][data-track-index="0"]').bounding_box()
        x, y = scroller["x"] + self.page.evaluate("HEADW") + 180, row["y"] + row["height"] / 2
        before_scroll = self.page.locator("#tlscroll").evaluate("s => s.scrollLeft")
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x - 100, y, steps=8)
        self.page.mouse.up()
        after_scroll = self.page.locator("#tlscroll").evaluate("s => s.scrollLeft")
        self.assertGreater(after_scroll, before_scroll + 70)

        # Middle mouse pans while the selection tool remains active.
        self.page.locator('#nleTools [data-t="sel"]').click()
        self.page.mouse.move(x, y)
        self.page.mouse.down(button="middle")
        self.page.mouse.move(x + 60, y, steps=8)
        self.page.mouse.up(button="middle")
        self.assertLess(self.page.locator("#tlscroll").evaluate("s => s.scrollLeft"), after_scroll - 40)
        self.page.locator('#nleTools [data-t="zoom"]').click()
        self.clip("red").click(modifiers=["Alt"])
        self.assertLess(self.page.evaluate("pps"), zoomed)
        self.assertEqual((self.job / "timeline.json").read_bytes(), saved)
        self.assertEqual(len(self.write_paths), saved_requests)
        self.page.screenshot(path=str(self.artifacts / "07-timeline-tools.png"))
        self.assertEqual(self.failed_saves, [])
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
