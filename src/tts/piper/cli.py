import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request


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

    read = commands.add_parser("read", help="Queue text for playback")
    read.add_argument("text", help="Text to speak")

    args = parser.parse_args()

    if args.command == "start":
        return systemctl("start")

    if args.command == "stop":
        return systemctl("stop")

    if args.command == "serve":
        from .server import main as serve

        serve()
        return 0

    try:
        if args.command == "status":
            status, body = request("/status")
            if status == 200 and body.get("service") == "tts-local":
                print(
                    f"Running; {body['queued']} waiting "
                    f"(capacity {body['queue_capacity']})."
                )
                return 0

            print("Unexpected response from local server.", file=sys.stderr)
            return 1

        status, body = request("/read", {"text": args.text})
        if status == 202:
            print("Queued.")
            return 0

        print(body.get("error", f"HTTP {status}"), file=sys.stderr)
        return 1

    except (urllib.error.URLError, TimeoutError, OSError):
        print(
            "Cannot reach the TTS service. Start it with: tts start",
            file=sys.stderr,
        )
        return 1
