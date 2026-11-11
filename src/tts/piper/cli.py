import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .markdown import MAX_DOCUMENT_LENGTH


SERVICE = "tts-local.service"
BASE_URL = "http://127.0.0.1:8765"


def systemctl(*args: str) -> int:
    try:
        result = subprocess.run(["systemctl", "--user", *args, SERVICE])
    except FileNotFoundError:
        print("systemctl is not installed.", file=sys.stderr)
        return 1

    return result.returncode


def request(path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            body = json.load(exc)
        except (ValueError, UnicodeDecodeError):
            body = {"error": f"HTTP {exc.code}"}
        return exc.code, body


def main() -> int:
    parser = argparse.ArgumentParser(prog="tts")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("start", help="Start the local TTS service")
    commands.add_parser("stop", help="Stop the service and clear the queue")
    commands.add_parser("status", help="Check the service and queue")
    commands.add_parser("serve", help="Run the service in the foreground")
    commands.add_parser("pause", help="Pause after the current message")
    commands.add_parser("resume", help="Resume queued playback")
    commands.add_parser("skip", help="Skip the current message")
    commands.add_parser("voices", help="List installed voices")

    read = commands.add_parser("read", help="Queue text for playback")
    read.add_argument("text", help="Text to speak")

    read.add_argument(
        "--voice",
        help="Use an installed voice instead of the configured default",
    )

    file_parser = commands.add_parser("file", help="Read a UTF-8 Markdown file")
    file_parser.add_argument("path", help="Path to the Markdown file")
    file_parser.add_argument("--voice", help="Use an installed voice")

    args = parser.parse_args()

    if args.command == "start":
        return systemctl("start")

    if args.command == "stop":
        return systemctl("stop")

    if args.command == "serve":
        from .server import main as serve

        serve()
        return 0

    if args.command == "voices":
        from .voices import available_voices, default_voice

        try:
            selected = default_voice()
            voices = available_voices()
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1

        if not voices:
            print("No voices installed.")
            return 0

        for voice in voices:
            suffix = " (default)" if voice == selected else ""
            print(voice + suffix)

        if selected not in voices:
            print(
                f"Configured default voice is not installed: {selected}",
                file=sys.stderr,
            )
            return 1

        return 0

    try:
        if args.command == "status":
            status, body = request("/status")

            if status == 200 and body.get("service") == "tts-local":
                queue_state = "paused" if body["paused"] else "active"
                print(
                    f"Running; queue {queue_state}; "
                    f"phase: {body['phase']}; "
                    f"{body['queued']} waiting "
                    f"(capacity {body['queue_capacity']})."
                )
                return 0

            print("Unexpected response from local server.", file=sys.stderr)
            return 1

        if args.command in ("pause", "resume", "skip"):
            status, body = request(f"/{args.command}", {})

            if status == 200:
                print(body["status"].capitalize() + ".")
                return 0

            print(body.get("error", f"HTTP {status}"), file=sys.stderr)
            return 1

        if args.command == "file":
            try:
                path = Path(args.path).expanduser()
                with path.open("r", encoding="utf-8-sig") as source:
                    text = source.read(MAX_DOCUMENT_LENGTH + 1)
                if len(text) > MAX_DOCUMENT_LENGTH:
                    raise ValueError(f"File exceeds {MAX_DOCUMENT_LENGTH} characters")
                if not text.strip():
                    raise ValueError("The file is empty")
            except (OSError, UnicodeError, ValueError) as error:
                message = f"Cannot read file: {error}"
                print(message, file=sys.stderr)
                try:
                    status, body = request("/read", {"text": message[:1000]})
                    if status != 202:
                        print("Could not queue the spoken error.", file=sys.stderr)
                except (urllib.error.URLError, TimeoutError, OSError):
                    print("TTS service is unavailable; cannot speak the error.",
                          file=sys.stderr)
                return 1
            payload = {"text": text, "format": "markdown"}
        else:
            payload = {"text": args.text}

        if args.voice is not None:
            payload["voice"] = args.voice

        status, body = request("/read", payload)

        if status == 202:
            print("Queued.")

            if "warning" in body:
                print(body["warning"], file=sys.stderr)

            return 0

        error = body.get("error", f"HTTP {status}")
        print(error, file=sys.stderr)
        if args.command == "file":
            spoken_status, _ = request("/read", {"text": f"Cannot read file: {error}"[:1000]})
            if spoken_status != 202:
                print("Could not queue the spoken error.", file=sys.stderr)
        return 1

    except (urllib.error.URLError, TimeoutError, OSError):
        print(
            "Cannot reach the TTS service. Start it with: tts start",
            file=sys.stderr,
        )
        return 1
