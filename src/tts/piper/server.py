import json
import logging
import os
import queue
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from piper import PiperVoice

HOST = "127.0.0.1"
PORT = 8765

MAX_QUEUE_SIZE = 20
MAX_TEXT_LENGTH = 500
MAX_REQUEST_BYTES = 4096

# Set TTS_VOICE to use another model. Its .onnx.json file must be alongside it.
DEFAULT_VOICE = Path.home() / "src/tts-local/en_US-hfc_female-medium.onnx"
VOICE_PATH = Path(os.environ.get("TTS_VOICE", DEFAULT_VOICE)).expanduser()

messages: queue.Queue[str] = queue.Queue(maxsize=MAX_QUEUE_SIZE)
log = logging.getLogger("tts")


def play_messages(voice: PiperVoice) -> None:
    """Process one message at a time, in queue order."""
    while True:
        text = messages.get()

        try:
            # WAV is held in memory, so no temporary audio files are needed.
            import io

            audio = io.BytesIO()
            with wave.open(audio, "wb") as wav_file:
                voice.synthesize_wav(text, wav_file)

            subprocess.run(
                    ["aplay", "-q"],
                    input=audio.getvalue(),
                    check=True,
                    )
        except Exception:
            log.exception("Failed to sunthesize or play a message")
        finally:
            messages.task_done()

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

        self.respond(
            200,
            {
                "service": "tts-local",
                "queued": messages.qsize(),
                "queue_capacity": MAX_QUEUE_SIZE,
            },
        )

    def do_POST(self) -> None:
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

        try:
            messages.put_nowait(text)
        except queue.Full:
            self.respond(429, {"error": "Speach queue is full"})
            return

        self.respond(202, {"status": "queued"})

    def log_message(self, format: str, *args: object) -> None:
        log.info("%s - %s", self.address_string(), format % args)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    log.info("Leading voice: %s", VOICE_PATH)
    voice = PiperVoice.load(VOICE_PATH)

    worker = threading.Thread(
        target=play_messages,
        args=(voice,),
        daemon=True,
    )
    worker.start()

    with ThreadingHTTPServer((HOST, PORT), Handler) as server:
        log.info("Listening on http://%s:%s", HOST, PORT)
        server.serve_forever()

if __name__ == "__main__":
    main()
