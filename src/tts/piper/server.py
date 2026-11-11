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
from .voices import default_voice, voice_path


HOST = "127.0.0.1"
PORT = 8765

MAX_QUEUE_SIZE = 20
MAX_TEXT_LENGTH = 100_000
MAX_REQUEST_BYTES = 600_100

log = logging.getLogger("tts")


class SpeechQueue:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.messages: deque[tuple[str, Path]] = deque()
        self.paused = False

        # The current message includes both synthesis and playback.
        self.current = False
        self.phase = "idle"
        self.skip_requested = False
        self.process: subprocess.Popen | None = None

    def add(self, text: str, model: Path) -> bool:
        with self.condition:
            if len(self.messages) >= MAX_QUEUE_SIZE:
                return False

            self.messages.append((text, model))
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

            if self.process is not None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    # It finished between our check and terminate().
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

            with text_path.open("rb") as text_file:
                synthesized = self.run_process(
                    [
                        sys.executable,
                        "-m",
                        "piper",
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

    def work(self) -> None:
        while True:
            with self.condition:
                self.condition.wait_for(
                    lambda: bool(self.messages) and not self.paused
                )

                text, model = self.messages.popleft()
                self.current = True
                self.phase = "synthesizing"
                self.skip_requested = False

            try:
                self.speak(text, model)
            except Exception:
                log.exception("Failed to synthesize or play a message")
            finally:
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

        if len(text) > MAX_TEXT_LENGTH:
            self.respond(
                400,
                {"error": f"Text exceeds {MAX_TEXT_LENGTH} characters"},
            )
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

        if not speech.add(text, model):
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

    worker = threading.Thread(target=speech.work, daemon=True)
    worker.start()

    with ThreadingHTTPServer((HOST, PORT), Handler) as server:
        log.info("Default voice: %s", model)
        log.info("Listening on http://%s:%s", HOST, PORT)
        server.serve_forever()


if __name__ == "__main__":
    main()
