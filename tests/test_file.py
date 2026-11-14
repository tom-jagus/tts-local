import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tts.piper import cli, synthesize, voices
from tts.piper.markdown import markdown_sections, SECTION_LIMIT
from tts.piper.server import SpeechQueue


class InferenceThreadTests(unittest.TestCase):
    def test_default_and_configured_threads(self):
        with patch.object(voices, 'read_config', return_value={}):
            self.assertEqual(voices.inference_threads(), 2)
        with patch.object(voices, 'read_config', return_value={'inference_threads': 1}):
            self.assertEqual(voices.inference_threads(), 1)

    def test_invalid_threads(self):
        for value in (True, 0, -1, 65, 1.5, '2'):
            with self.subTest(value=value), patch.object(
                voices, 'read_config', return_value={'inference_threads': value}
            ):
                with self.assertRaises(ValueError):
                    voices.inference_threads()

    def test_synthesis_worker_configures_session(self):
        import onnxruntime
        import piper.__main__ as piper_cli

        with patch('sys.argv', ['worker', '2', '--model', 'voice']), \
             patch.object(onnxruntime, 'InferenceSession') as session, \
             patch.object(piper_cli, 'main') as main:
            def load():
                onnxruntime.InferenceSession('voice', sess_options=onnxruntime.SessionOptions())
            main.side_effect = load
            synthesize.main()
            options = session.call_args.kwargs['sess_options']
            self.assertEqual(options.intra_op_num_threads, 2)
            self.assertEqual(options.inter_op_num_threads, 1)
            self.assertEqual(__import__('sys').argv, ['worker', '--model', 'voice'])

    def test_queue_passes_thread_count_to_worker(self):
        speech = SpeechQueue(threads=3)
        with patch.object(speech, 'run_process', return_value=True) as run:
            speech.speak('hello', Path('voice.onnx'))
        command = run.call_args_list[0].args[0]
        self.assertEqual(command[1:4], ['-m', 'tts.piper.synthesize', '3'])
        self.assertEqual(run.call_args_list[1].args[1], 'playing')


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
