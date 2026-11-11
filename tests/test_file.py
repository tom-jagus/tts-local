import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tts.piper import cli
from tts.piper.markdown import markdown_sections, SECTION_LIMIT
from tts.piper.server import SpeechQueue


class MarkdownTests(unittest.TestCase):
    def test_cleanup(self):
        sections = markdown_sections('# Title\n\n**Hello**\nworld [guide](https://example.com).\n\n```python\nsecret()\n```')
        text = ' '.join(sections)
        self.assertIn('Hello world guide.', text)
        self.assertIn('python code block.', text)
        self.assertNotIn('secret', text)
        self.assertNotIn('https:', text)

    def test_headings_and_bounds(self):
        sections = markdown_sections('# One\n\nFirst.\n\n## Two\n\n' + 'word ' * 3000)
        self.assertIn('One', sections[0])
        self.assertNotIn('Two', sections[0])
        self.assertTrue(all(len(s) <= SECTION_LIMIT for s in sections))

    def test_file_error_is_spoken(self):
        with patch('sys.argv', ['tts', 'file', '/nonexistent/tts-test.md']), patch.object(cli, 'request', return_value=(202, {})) as request:
            self.assertEqual(cli.main(), 1)
            self.assertIn('Cannot read file', request.call_args.args[1]['text'])

    def test_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.md'
            path.write_bytes(b'\xff')
            with patch('sys.argv', ['tts', 'file', str(path)]), patch.object(cli, 'request', return_value=(202, {})) as request:
                self.assertEqual(cli.main(), 1)
                request.assert_called_once()

    def test_pause_then_skip_discards_remaining_file(self):
        speech = SpeechQueue()
        first = threading.Event()
        release = threading.Event()
        next_job = threading.Event()
        played = []

        def speak(text, model):
            played.append(text)
            if text == 'first':
                first.set()
                release.wait(2)
            if text == 'next':
                next_job.set()

        speech.speak = speak
        speech.add(['first', 'unwanted'], Path('voice'))
        speech.add(['next'], Path('voice'))
        threading.Thread(target=speech.work, daemon=True).start()
        self.assertTrue(first.wait(2))
        speech.pause()
        release.set()
        deadline = time.monotonic() + 2
        while speech.status()['phase'] != 'paused' and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(speech.status()['phase'], 'paused')
        self.assertTrue(speech.skip())
        self.assertFalse(next_job.wait(0.05))
        speech.resume()
        self.assertTrue(next_job.wait(2))
        self.assertEqual(played, ['first', 'next'])


if __name__ == '__main__':
    unittest.main()
