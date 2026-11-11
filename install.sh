#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
EXPECTED_DIR="$HOME/src/tts-local"

CONNECTOR_DIR="$PROJECT_DIR/pi-tts-connector"
CONNECTOR_FILE="$CONNECTOR_DIR/index.ts"

SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/tts-local.service"

if [[ "$PROJECT_DIR" != "$EXPECTED_DIR" ]]; then
    printf 'Install this repository at %s first.\n' "$EXPECTED_DIR" >&2
    exit 1
fi

if [[ "$HOME" == *[[:space:]%]* ]]; then
    echo "Home paths containing whitespace or '%' are not supported." >&2
    exit 1
fi

for command in uv aplay systemctl pi; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command" >&2
        exit 1
    fi
done

if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "Cannot connect to the systemd user service manager." >&2
    exit 1
fi

for file in \
    "$CONNECTOR_FILE"
do
    if [[ ! -f "$file" ]]; then
        printf 'Required connector file not found: %s\n' "$file" >&2
        exit 1
    fi
done

cd "$PROJECT_DIR"

echo "Checking Python dependencies..."
uv sync

# Ensure that the terminal and systemd user manager resolve the same XDG
# locations. Otherwise installation and the running service could use
# different config files or voice directories.
uv run python - <<'PY'
import os
import subprocess
import sys
from pathlib import Path

from tts.piper.voices import config_file, voices_directory, xdg_directory

try:
    result = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        check=True,
        capture_output=True,
        text=True,
    )
except (OSError, subprocess.CalledProcessError) as error:
    print(f"Cannot inspect systemd user environment: {error}", file=sys.stderr)
    sys.exit(1)

manager_env = {}
for line in result.stdout.splitlines():
    key, separator, value = line.partition("=")
    if separator:
        manager_env[key] = value

home = Path.home()


def manager_path(variable: str, fallback: Path) -> Path:
    value = manager_env.get(variable, "")
    candidate = Path(value) if value else fallback
    return candidate if candidate.is_absolute() else fallback


manager_config = manager_path(
    "XDG_CONFIG_HOME", home / ".config"
) / "tts-local" / "config.toml"

manager_voices = manager_path(
    "XDG_DATA_HOME", home / ".local" / "share"
) / "tts-local" / "voices"

if config_file() != manager_config or voices_directory() != manager_voices:
    print("The terminal and systemd user manager use different XDG paths.",
          file=sys.stderr)
    print(f"Terminal config: {config_file()}", file=sys.stderr)
    print(f"Systemd config:  {manager_config}", file=sys.stderr)
    print(f"Terminal voices: {voices_directory()}", file=sys.stderr)
    print(f"Systemd voices:  {manager_voices}", file=sys.stderr)
    print("Align the XDG environment before running the installer.",
          file=sys.stderr)
    sys.exit(1)
PY

CONFIG_FILE="$(
    uv run python -c 'from tts.piper.voices import config_file; print(config_file())'
)"
VOICES_DIR="$(
    uv run python -c 'from tts.piper.voices import voices_directory; print(voices_directory())'
)"
DEFAULT_VOICE="$(
    uv run python -c 'from tts.piper.voices import DEFAULT_VOICE; print(DEFAULT_VOICE)'
)"

mkdir -p "$(dirname -- "$CONFIG_FILE")" "$VOICES_DIR"

if [[ -e "$CONFIG_FILE" ]]; then
    printf 'Configuration already exists; leaving it unchanged: %s\n' \
        "$CONFIG_FILE"
else
    # Exclusive creation prevents overwriting a file created concurrently.
    if (
        set -o noclobber
        printf 'default_voice = "%s"\n' "$DEFAULT_VOICE" > "$CONFIG_FILE"
    ); then
        printf 'Created configuration: %s\n' "$CONFIG_FILE"
    else
        printf 'Could not create configuration: %s\n' "$CONFIG_FILE" >&2
        exit 1
    fi
fi

MODEL="$VOICES_DIR/$DEFAULT_VOICE.onnx"
MODEL_CONFIG="$VOICES_DIR/$DEFAULT_VOICE.onnx.json"

OLD_MODEL="$PROJECT_DIR/$DEFAULT_VOICE.onnx"
OLD_MODEL_CONFIG="$PROJECT_DIR/$DEFAULT_VOICE.onnx.json"

if [[ -f "$MODEL" && -f "$MODEL_CONFIG" ]]; then
    echo "Default voice files are already present: $DEFAULT_VOICE"
elif [[ -e "$MODEL" || -e "$MODEL_CONFIG" ]]; then
    echo "The default voice is only partially installed." >&2
    echo "Inspect these files before retrying:" >&2
    printf '  %s\n  %s\n' "$MODEL" "$MODEL_CONFIG" >&2
    exit 1
elif [[ -f "$OLD_MODEL" && -f "$OLD_MODEL_CONFIG" ]]; then
    echo "Copying the existing default voice from the repository..."
    cp -n -- "$OLD_MODEL" "$MODEL"
    cp -n -- "$OLD_MODEL_CONFIG" "$MODEL_CONFIG"
else
    echo "Downloading default voice: $DEFAULT_VOICE"
    uv run python -m piper.download_voices \
        --download-dir "$VOICES_DIR" \
        "$DEFAULT_VOICE"
fi

# An existing config may name a different default voice. Do not overwrite it
# or silently change it to the bundled default.
uv run python - <<'PY'
import sys

from tts.piper.voices import default_voice, voice_path

try:
    name = default_voice()
    path = voice_path(name)
except ValueError as error:
    print(f"Configured default voice is unavailable: {error}", file=sys.stderr)
    sys.exit(1)

print(f"Configured default voice: {name}")
print(f"Voice model: {path}")
PY

if uv tool list | grep -qE '^tts v'; then
    echo "A uv tool named tts is already installed; leaving it unchanged."
    echo "Check that it points to this checkout if the command behaves unexpectedly."
else
    echo "Installing the tts command..."
    uv tool install --editable .
fi

TTS_BIN="$(uv tool dir --bin)/tts"

if [[ ! -x "$TTS_BIN" ]]; then
    printf 'Installed tts command not found: %s\n' "$TTS_BIN" >&2
    exit 1
fi

mkdir -p "$SERVICE_DIR"

if [[ -e "$SERVICE_FILE" ]]; then
    printf 'Service file already exists; leaving it unchanged: %s\n' \
        "$SERVICE_FILE"
    printf 'Check that its ExecStart points to: %s serve\n' "$TTS_BIN"
else
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Local text-to-speech queue

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$TTS_BIN serve
KillMode=control-group
TimeoutStopSec=5

[Install]
WantedBy=default.target
EOF
    printf 'Created service file: %s\n' "$SERVICE_FILE"
fi

systemctl --user daemon-reload

# Pi stores personal package paths relative to its settings file.
if uv run python - "$CONNECTOR_FILE" <<'PY'
import json
import sys
from pathlib import Path

target = Path(sys.argv[1]).resolve()
settings_file = Path.home() / ".pi/agent/settings.json"

if not settings_file.exists():
    sys.exit(1)

try:
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    print(f"Cannot read Pi settings: {error}", file=sys.stderr)
    sys.exit(2)

sources = []

for package in settings.get("packages", []):
    if isinstance(package, str):
        sources.append(package)
    elif isinstance(package, dict):
        source = package.get("source")
        if isinstance(source, str):
            sources.append(source)

for extension in settings.get("extensions", []):
    if isinstance(extension, str):
        sources.append(extension)

for source in sources:
    if source.startswith(("npm:", "git:", "http:", "https:", "builtin:")):
        continue

    path = Path(source).expanduser()
    if not path.is_absolute():
        path = settings_file.parent / path

    if path.resolve() == target:
        sys.exit(0)

sys.exit(1)
PY
then
    echo "Pi connector is already registered."
else
    result=$?

    if [[ "$result" -ne 1 ]]; then
        echo "Could not safely check Pi settings; stopping." >&2
        exit "$result"
    fi

    echo "Registering the Pi connector..."
    pi install "$CONNECTOR_FILE"
fi

echo
echo "Installation complete."
echo
echo "Start and test the TTS service:"
echo '  tts start'
echo '  tts status'
echo '  tts voices'
echo '  tts read "Hello from the local speech queue."'
echo
echo "Run /reload in an existing Pi session to load the connector."
echo "Automatic reading starts on in each new or reloaded Pi session."
echo
echo "The service has not been enabled for automatic startup."
echo "To enable it for future user sessions:"
echo '  systemctl --user enable tts-local.service'
echo
echo "If tts is not on your PATH, run: uv tool update-shell"
