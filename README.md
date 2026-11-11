# TTS Local

TTS Local is a local text-to-speech service for Linux. It accepts text over a
local HTTP endpoint, queues messages, and plays them through the computer's
sound output. It uses [Piper](https://github.com/OHF-Voice/piper1-gpl) for
speech synthesis and `aplay` for playback.

The repository also contains a [Pi](https://pi.dev/) extension that reads
assistant messages through the same queue. Other local applications can submit
text to the HTTP endpoint. There is no YouTube chat connector in this
repository.

## Requirements

- Linux with a working systemd user session and audio output.
- [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer.
- `aplay`, provided by the ALSA utilities package on many distributions.
- Pi, Node.js and npm for the Pi connector and its Markdown parser.
- Network access for installation and the initial voice download.

The installer requires all of these commands, including Pi and npm. Once the
voice and dependencies are installed, synthesis runs locally.

## Installation

Clone the repository into `~/src/tts-local`. The current installer and default
voice path expect this location.

```bash
mkdir -p ~/src
cd ~/src
git clone https://github.com/tom-jagus/tts-local.git
cd tts-local
./install.sh
```

The installer synchronizes Python dependencies, downloads the default voice if
needed, installs the `tts` command as an editable uv tool, creates a systemd
user service, and installs and registers the Pi connector. It does not start
or enable the service. It leaves an existing `tts` uv tool or service file
unchanged, so check that they point to this checkout if you already have them.

The Pi registration and editable tool refer to files in this checkout. Moving
or deleting the directory will break those references. The installer currently
does not support home paths containing whitespace or `%`.

If `tts` is not found after installation, run `uv tool update-shell` and open
a new terminal. Run `/reload` in an existing Pi session to load the connector.
Reloading turns automatic reading on.

## Using the service

Start the service and submit a message:

```bash
tts start
tts status
tts read "Hello from TTS Local."
```

`tts read` returns when the message is queued, not when playback finishes.
Messages play in the order the service accepts them. Use these commands to
control playback:

| Command | Effect |
| --- | --- |
| `tts start` | Start the user service. |
| `tts stop` | Stop the systemd user service, interrupt its playback, and discard its pending messages. |
| `tts status` | Show whether the queue is paused, the current phase, and the number waiting. |
| `tts pause` | Finish the current message, then hold the queue. |
| `tts resume` | Resume queued playback. |
| `tts skip` | Interrupt the current message. The next message starts unless the queue is paused. |
| `tts serve` | Run the service in the foreground for debugging. |

The queue is in memory. Stopping or restarting the service discards waiting
messages. It holds up to 20 waiting messages, in addition to the current
message. Text is limited to 100,000 characters per request. Long messages can
take time to synthesize and delay other queued messages.

To start the service automatically in future user sessions, run:

```bash
systemctl --user enable tts-local.service
```

This does not start it immediately. Use `tts start` for the current session.

## Pi connector

The connector automatically queues completed assistant text, including text
sent before a tool call and final replies. It does not read tool results or
thinking content. Automatic reading starts on for each new or reloaded Pi
session, and the footer shows `TTS on` or `TTS off`.

| Pi command | Effect |
| --- | --- |
| `/tts` | Queue the latest completed assistant text once, even if automatic reading is off. |
| `/tts on` | Enable automatic reading for subsequent messages. |
| `/tts off` | Disable automatic reading for subsequent messages. |

Turning automatic reading off does not remove messages already submitted to
the service. Use `tts skip` to interrupt the current message, or `tts stop` to
stop the systemd user service and discard its queue. If you started the service
in the foreground with `tts serve`, press Ctrl+C in that terminal instead.

The connector parses Markdown before submitting text. It removes formatting
markers, joins wrapped lines within paragraphs, reads link labels without their
URLs, and replaces code blocks with a short label such as "Python code block."
Inline code remains in the spoken text. Tables and other formatted content may
not always sound natural.

## Voice selection

The default voice is `en_US-hfc_female-medium`. The installer downloads its
`.onnx` model and `.onnx.json` configuration into the repository directory.
Voice models have their own terms, so check the terms for a voice before using
it. Piper's [voice documentation](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md)
explains how to find and download voices.

The service reads the `TTS_VOICE` environment variable as a model path. The
corresponding `.onnx.json` file must be alongside the model. For example, to
try another voice in the foreground:

```bash
TTS_VOICE=/path/to/voice.onnx tts serve
```

Stop the user service first, because both instances use the same local port.
Setting `TTS_VOICE` in a terminal does not automatically configure the systemd
user service; it must be set in that service's environment separately.

## Local HTTP API

The service listens on `127.0.0.1:8765`. To submit text directly:

```bash
curl --fail-with-body \
  -X POST http://127.0.0.1:8765/read \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello from a local application."}'
```

A successful request returns HTTP `202` after the message enters the queue.
The service also provides `GET /status` and `POST /pause`, `/resume`, and
`/skip`. A full queue returns HTTP `429`; skipping while idle returns HTTP
`409`.

The endpoint has no authentication. It listens only on localhost, but other
processes on the computer can submit text. If OBS captures the TTS output,
spoken Pi responses may be audible on stream.

## Troubleshooting

Check the user service logs if it fails to start or a queued message does not
play:

```bash
journalctl --user -u tts-local.service -n 50 --no-pager
```

If the service is running but there is no sound, check that `aplay` can use
your intended audio output and that the systemd user session has access to it.
The service generates speech and plays it on the machine running the service.

If Pi cannot find `markdown-it`, check that `npm ci` completed in
`pi-tts-connector/`, then run `/reload` in Pi. If Pi reports that it cannot
reach TTS, check `tts status` and the service logs.

For foreground debugging, stop the user service before running `tts serve`.
Press Ctrl+C to stop the foreground process.
