"""Synthesize sections with one voice session per message."""

import json
import sys
import wave

from .synthesize import configure_inference


def main() -> None:
    threads = int(sys.argv[1])
    model = sys.argv[2]
    configure_inference(threads)
    from piper import PiperVoice

    voice = PiperVoice.load(model)
    for line in sys.stdin:
        request = json.loads(line)
        with wave.open(request["output"], "wb") as output:
            voice.synthesize_wav(request["text"], output)
        print(json.dumps({"ok": True}), flush=True)


if __name__ == "__main__":
    main()
