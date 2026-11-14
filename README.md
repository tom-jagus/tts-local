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
- Pi for the Pi connector.
- Network access for installation and the initial voice download.

The installer requires Pi as well as the other listed commands. Once the voice
and dependencies are installed, synthesis runs locally.

## Installation

Clone the repository into `~/src/tts-local`. The current installer expects
this location.

```bash
mkdir -p ~/src
cd ~/src
git clone https://github.com/tom-jagus/tts-local.git
cd tts-local
./install.sh
```

The installer synchronizes Python dependencies, prepares the default voice and
configuration, installs the `tts` command as an editable uv tool, creates a
systemd user service, and installs and registers the Pi connector. It does not
start or enable the service. It leaves an existing `tts` uv tool or service
file unchanged, so check that they point to this checkout if you already have
them.

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
| `tts pause` | Finish the current playback section, then hold the queue. |
| `tts resume` | Resume queued playback. |
| `tts skip` | Interrupt the current message. The next message starts unless the queue is paused. |
| `tts serve` | Run the service in the foreground for debugging. |

The queue is in memory. Stopping or restarting the service discards waiting
messages. It holds up to 20 waiting messages, in addition to the current
message. Plain-text requests are limited to 100,000 characters; Markdown
requests are limited to 2,000,000 characters. Both formats are split into
playback sections of at most 1,000 characters. Long messages can still take
time to synthesize and delay other queued messages.

To start the service automatically in future user sessions, run:

```bash
systemctl --user enable tts-local.service
```

This does not start it immediately. Use `tts start` for the current session.

### Read a Markdown file

Queue a UTF-8 Markdown file, optionally choosing an installed voice:

```bash
tts file ~/path/to/document.md
tts file --voice en_US-hfc_male-medium ~/path/to/document.md
```

The service splits the document into playback sections at headings and splits
large sections further. The entire document remains one queue item, so other
messages do not interleave with it. `tts pause` waits for the current section
to finish; `tts resume` continues the document, and `tts skip` discards the
rest of it.

For an unreadable file, invalid UTF-8, or an oversized file, the command prints
an error and attempts to queue a spoken explanation. The explanation cannot
play if the service is unavailable, and it may wait behind other messages.

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

Turning automatic reading off prevents new automatic submissions, but does not
cancel requests already waiting in the connector or messages already submitted
to the service. Pending connector requests can reach the service after it is
restarted. Use `tts skip` to interrupt only the current message, or `tts stop`
to stop the systemd user service and discard its queue. If you started the
service in the foreground with `tts serve`, press Ctrl+C in that terminal
instead.

The connector submits Markdown to the service, which also prepares Markdown
files for speech. The service removes formatting markers, joins wrapped lines
within paragraphs, reads link labels without their URLs, and replaces code
blocks with a short label such as "Python code block." Inline code remains in
the spoken text. Pi messages do not split at headings, although long messages
are still split into playback sections. Tables and other formatted content may
not sound natural.

## Voice selection

List installed voices and use a particular voice for one message:

```bash
tts voices
tts read --voice en_US-hfc_male-medium "Hello from another voice."
```

If a requested voice is invalid or not installed, the service uses the
configured default and returns a warning. Pi requests do not select a voice,
so Pi uses the default. A queued message keeps the voice selected when it was
submitted.

The installer creates `~/.config/tts-local/config.toml` if it does not already
exist. Set `default_voice` to the name of an installed voice:

```toml
default_voice = "en_US-hfc_female-medium"
```

The service reads the configured default when accepting each message. It also
checks the default when it starts, so an invalid or missing default prevents
startup. Messages already in the queue retain their selected voices.

Voice models and their matching `.onnx.json` files live in
`~/.local/share/tts-local/voices/`. The installer prepares
`en_US-hfc_female-medium`, copying existing repository files when available or
downloading the voice otherwise. It does not overwrite an existing config or
voice. If an existing config names a different, unavailable default voice, the
installer reports the problem rather than changing it.

To download another voice, first ensure `XDG_DATA_HOME` is unset or is an
absolute path, then run:

```bash
cd ~/src/tts-local
uv run python -m piper.download_voices \
  --download-dir "${XDG_DATA_HOME:-$HOME/.local/share}/tts-local/voices" \
  en_US-hfc_male-medium
```

Both the `.onnx` and `.onnx.json` files must be present for a voice to appear
in `tts voices`. Voice models have their own terms, so check those terms before
using a voice. Piper's [voice documentation](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md)
explains how to find voices.

### Inference threads

Piper uses two ONNX inference threads by default. To choose a different count,
add `inference_threads` to `~/.config/tts-local/config.toml`:

```toml
inference_threads = 1
```

The value must be an integer between 1 and 64. Restart the service to apply a
change. Stopping or restarting the service interrupts playback and discards
its queue. Fewer inference threads can leave more CPU capacity for other
applications, but synthesis may take longer. This setting does not limit all
CPU activity in the Piper process.

### Persistent message worker

By default, the service loads the selected Piper voice once per queued message
and keeps it loaded until that message finishes or is skipped. This avoids
reloading the model between sections, but keeps its memory in use during
playback and while the queue is paused. To use a separate Piper process for
each section instead, add this setting to the same configuration file:

```toml
persistent_worker = false
```

The setting must be a boolean and is read when the service starts. Restarting
the service applies it but interrupts playback and discards the queue. The
service synthesizes each section after the preceding section has played, so
there can still be a pause between sections.

The service uses absolute `XDG_CONFIG_HOME` and `XDG_DATA_HOME` values when
set, falling back to `~/.config` and `~/.local/share` otherwise. The installer
checks that the terminal and systemd user manager resolve the same locations.
If they differ, align their environments before installation.

## Local HTTP API

The service listens on `127.0.0.1:8765`. To submit text directly:

```bash
curl --fail-with-body \
  -X POST http://127.0.0.1:8765/read \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello from a local application."}'
```

A successful request returns HTTP `202` after the message enters the queue.
An optional `voice` field selects an installed voice; an invalid or missing
voice falls back to the default with a warning in the response. Omitting the
field uses the default. Add `"format": "markdown"` to prepare Markdown for
speech; the default format is plain `text`. Markdown requests split playback
sections at headings by default. Set `"split_headings": false` to avoid those
splits; long sections can still be split. The field must be a boolean, including
for plain-text requests, where it has no effect.

The service also provides `GET /status` and `POST /pause`, `/resume`, and
`/skip`. A full queue returns HTTP `429`; skipping while idle returns HTTP `409`.

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

If Pi reports that it cannot reach TTS, check `tts status` and the service
logs. After updating the Pi connector, run `/reload` in Pi.

For foreground debugging, stop the user service before running `tts serve`.
Press Ctrl+C to stop the foreground process.
