import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tts.piper import cli, server, synthesize, voices
from tts.piper.markdown import markdown_sections, text_sections, SECTION_LIMIT
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

    def test_persistent_worker_configuration(self):
        with patch.object(voices, 'read_config', return_value={}):
            self.assertTrue(voices.persistent_worker())
        with patch.object(voices, 'read_config', return_value={'persistent_worker': False}):
            self.assertFalse(voices.persistent_worker())
        with patch.object(voices, 'read_config', return_value={'persistent_worker': 'false'}):
            with self.assertRaises(ValueError):
                voices.persistent_worker()

    def test_persistent_worker_reused_and_closed(self):
        speech = SpeechQueue(persistent=True)
        worker = unittest.mock.Mock()
        worker.stdout.readline.side_effect = ['{"ok": true}\n', '{"ok": true}\n']
        worker.poll.return_value = None
        with patch.object(server.subprocess, 'Popen', return_value=worker) as popen:
            self.assertTrue(speech.synthesize_section('first', Path('voice'), Path('one')))
            self.assertTrue(speech.synthesize_section('second', Path('voice'), Path('two')))
            self.assertEqual(popen.call_count, 1)
            speech.close_worker()
        self.assertEqual(worker.stdin.write.call_count, 2)
        worker.terminate.assert_called_once()
        worker.wait.assert_called_once()
        self.assertIsNone(speech.worker)

    def test_skip_terminates_worker_and_playback(self):
        speech = SpeechQueue(persistent=True)
        speech.current = True
        speech.process = unittest.mock.Mock()
        speech.worker = unittest.mock.Mock()
        self.assertTrue(speech.skip())
        speech.process.terminate.assert_called_once()
        speech.worker.terminate.assert_called_once()

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

    def test_plain_text_sections_preserve_content_and_bound_length(self):
        text = ('A sentence with **literal** text. ' * 90) + ('x' * 1500)
        sections = text_sections(text)
        self.assertGreater(len(sections), 1)
        self.assertEqual(''.join(sections), text)
        self.assertTrue(all(len(section) <= 1000 for section in sections))

    def test_plain_text_skips_whitespace_only_sections(self):
        sections = text_sections('first' + ' ' * 3000 + 'last')
        self.assertTrue(all(section.strip() for section in sections))
        self.assertEqual(''.join(sections).split(), ['first', 'last'])

    def test_plain_text_request_queues_one_item_with_bounded_sections(self):
        text = 'word ' * 500
        body = json.dumps({'text': text}).encode()
        handler = object.__new__(server.Handler)
        handler.path = '/read'
        handler.headers = {'Content-Length': str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.respond = unittest.mock.Mock()
        with patch.object(server, 'default_voice', return_value='voice'), \
             patch.object(server, 'voice_path', return_value=Path('voice')), \
             patch.object(server.speech, 'add', return_value=True) as add:
            handler.do_POST()
        sections, model = add.call_args.args
        self.assertEqual(''.join(sections), text.strip())
        self.assertGreater(len(sections), 1)
        self.assertTrue(all(len(section) <= 1000 for section in sections))
        self.assertEqual(model, Path('voice'))
        handler.respond.assert_called_once_with(202, {'status': 'queued', 'voice': 'voice'})

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
