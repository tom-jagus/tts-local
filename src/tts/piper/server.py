import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .voices import default_voice, inference_threads, persistent_worker, voice_path
from .markdown import MAX_DOCUMENT_LENGTH, markdown_sections, text_sections


HOST = "127.0.0.1"
PORT = 8765

MAX_QUEUE_SIZE = 20
MAX_TEXT_LENGTH = 100_000
MAX_REQUEST_BYTES = 12_000_200

log = logging.getLogger("tts")


class SpeechQueue:
    def __init__(self, threads: int = 2, persistent: bool = False) -> None:
        self.threads = threads
        self.persistent = persistent
        self.condition = threading.Condition()
        self.messages: deque[tuple[list[str], Path]] = deque()
        self.paused = False

        # The current message includes both synthesis and playback.
        self.current = False
        self.phase = "idle"
        self.skip_requested = False
        self.process: subprocess.Popen | None = None
        self.worker: subprocess.Popen | None = None

    def add(self, sections: list[str], model: Path) -> bool:
        with self.condition:
            if len(self.messages) >= MAX_QUEUE_SIZE:
                return False

            self.messages.append((sections, model))
            self.condition.notify_all()
            return True

    def status(self) -> dict:
        with self.condition:
            return {
                "service": "tts-local",
                "queued": len(self.messages),
                "queue_capacity": MAX_QUEUE_SIZE,
                "paused": self.paused,
                "phase": self.phase,
            }

    def pause(self) -> None:
        with self.condition:
            self.paused = True
            self.condition.notify_all()

    def resume(self) -> None:
        with self.condition:
            self.paused = False
            self.condition.notify_all()

    def skip(self) -> bool:
        with self.condition:
            if not self.current:
                return False

            self.skip_requested = True
            self.condition.notify_all()

            for process in (self.process, self.worker):
                if process is not None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass

            return True

    def run_process(self, command: list[str], phase: str, **kwargs) -> bool:
        """Run a child process. Return False when the message was skipped."""
        with self.condition:
            if self.skip_requested:
                return False

            self.phase = phase
            process = subprocess.Popen(command, **kwargs)
            self.process = process

        return_code = process.wait()

        with self.condition:
            self.process = None
            skipped = self.skip_requested

        if skipped:
            return False

        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)

        return True

    def speak(self, text: str, model: Path) -> None:
        # Temporary files avoid putting the complete WAV in server memory.
        with tempfile.TemporaryDirectory(prefix="tts-local-") as directory:
            directory_path = Path(directory)
            text_path = directory_path / "message.txt"
            audio_path = directory_path / "speech.wav"

            text_path.write_text(text, encoding="utf-8")

            if self.persistent:
                synthesized = self.synthesize_section(text, model, audio_path)
            else:
                with text_path.open("rb") as text_file:
                    synthesized = self.run_process(
                        [
                            sys.executable,
                            "-m",
                            "tts.piper.synthesize",
                            str(self.threads),
                            "--model",
                            str(model),
                            "--output_file",
                            str(audio_path),
                        ],
                        "synthesizing",
                        stdin=text_file,
                    )

            if not synthesized:
                return

            self.run_process(
                ["aplay", "-q", str(audio_path)],
                "playing",
            )

    def synthesize_section(self, text: str, model: Path, output: Path) -> bool:
        with self.condition:
            if self.skip_requested:
                return False
            self.phase = "synthesizing"
            if self.worker is None:
                self.worker = subprocess.Popen(
                    [sys.executable, "-m", "tts.piper.persistent",
                     str(self.threads), str(model)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    text=True, encoding="utf-8", bufsize=1,
                )
            worker = self.worker

        try:
            worker.stdin.write(json.dumps({"text": text, "output": str(output)}) + "\n")
            worker.stdin.flush()
            response = worker.stdout.readline()
        except (BrokenPipeError, OSError):
            with self.condition:
                if self.skip_requested:
                    return False
            raise
        with self.condition:
            if self.skip_requested:
                return False
        if not response or json.loads(response) != {"ok": True}:
            raise RuntimeError("Piper worker failed to synthesize a section")
        return True

    def close_worker(self) -> None:
        with self.condition:
            worker = self.worker
            self.worker = None
        if worker is not None:
            if worker.poll() is None:
                try:
                    worker.terminate()
                except ProcessLookupError:
                    pass
            try:
                worker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
            worker.stdin.close()
            worker.stdout.close()

    def work(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(
                    lambda: bool(self.messages) and not self.paused
                )

                sections, model = self.messages.popleft()
                self.current = True
                self.phase = "synthesizing"
                self.skip_requested = False

            try:
                for text in sections:
                    with self.condition:
                        if self.paused:
                            self.phase = "paused"
                        self.condition.wait_for(
                            lambda: not self.paused or self.skip_requested
                        )
                        if self.skip_requested:
                            break
                    self.speak(text, model)
            except Exception:
                log.exception("Failed to synthesize or play a message")
            finally:
                self.close_worker()
                with self.condition:
                    self.current = False
                    self.phase = "idle"
                    self.process = None
                    self.skip_requested = False
                    self.condition.notify_all()


speech = SpeechQueue()


class Handler(BaseHTTPRequestHandler):
    def respond(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/status":
            self.respond(404, {"error": "Not found"})
            return

        self.respond(200, speech.status())

    def do_POST(self) -> None:
        if self.path == "/pause":
            speech.pause()
            self.respond(200, {"status": "paused"})
            return

        if self.path == "/resume":
            speech.resume()
            self.respond(200, {"status": "resumed"})
            return

        if self.path == "/skip":
            if speech.skip():
                self.respond(200, {"status": "skipping"})
            else:
                self.respond(409, {"error": "Nothing is playing"})
            return

        if self.path != "/read":
            self.respond(404, {"error": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.respond(400, {"error": "Invalid Content-Length"})
            return

        if length <= 0 or length > MAX_REQUEST_BYTES:
            self.respond(400, {"error": "Invalid request size"})
            return

        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.respond(400, {"error": "Expected JSON"})
            return

        text = payload.get("text") if isinstance(payload, dict) else None

        if not isinstance(text, str) or not text.strip():
            self.respond(400, {"error": "Text must be a non-empty string"})
            return

        text = text.strip()

        format_name = payload.get("format", "text")
        if format_name not in ("text", "markdown"):
            self.respond(400, {"error": "Format must be text or markdown"})
            return
        limit = MAX_DOCUMENT_LENGTH if format_name == "markdown" else MAX_TEXT_LENGTH
        if len(text) > limit:
            self.respond(400, {"error": f"Text exceeds {limit} characters"})
            return

        split_headings = payload.get("split_headings", True)

        if not isinstance(split_headings, bool):
            self.respond(400, {"error": "split_headings must be a boolean"})
            return

        sections = (
            markdown_sections(text, split_headings=split_headings)
            if format_name == "markdown"
            else text_sections(text)
        )

        if not sections:
            self.respond(400, {"error": "No readable content found"})
            return

        requested_voice = payload.get("voice")
        warning = None

        try:
            selected_voice = default_voice()

            if requested_voice is not None:
                try:
                    model = voice_path(requested_voice)
                    selected_voice = requested_voice
                except ValueError:
                    model = voice_path(selected_voice)
                    warning = (
                        f"Voice {requested_voice!r} is invalid or not installed; "
                        f"using default voice {selected_voice!r}."
                    )
            else:
                model = voice_path(selected_voice)

        except ValueError as error:
            # The configured default must be valid and installed.
            self.respond(400, {"error": str(error)})
            return

        if not speech.add(sections, model):
            self.respond(429, {"error": "Speech queue is full"})
            return

        response = {"status": "queued", "voice": selected_voice}

        if warning:
            response["warning"] = warning

        self.respond(202, response)

    def log_message(self, format: str, *args: object) -> None:
        log.info("%s - %s", self.address_string(), format % args)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    model = voice_path(default_voice())
    speech.threads = inference_threads()
    speech.persistent = persistent_worker()

    worker = threading.Thread(target=speech.work, daemon=True)
    worker.start()

    with ThreadingHTTPServer((HOST, PORT), Handler) as server:
        log.info("Default voice: %s", model)
        log.info("Piper inference threads: %d", speech.threads)
        log.info("Persistent message worker: %s", speech.persistent)
        log.info("Listening on http://%s:%s", HOST, PORT)
        server.serve_forever()


if __name__ == "__main__":
    main()
