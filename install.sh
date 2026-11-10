#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
EXPECTED_DIR="$(cd -- "$HOME/src" && pwd -P)/tts-local"
VOICE="en_US-hfc_female-medium"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/tts-local.service"

if [[ "$PROJECT_DIR" != "$EXPECTED_DIR" ]]; then
    printf 'Install this repository at %s first.\n' "$EXPECTED_DIR" >&2
    exit 1
fi

for command in uv aplay systemctl; do
    if ! command -v "$command" >/dev/null 2>&1; then
        printf 'Required command not found: %s\n' "$command" >&2
        exit 1
    fi
done

if ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "Cannot connect to the systemd user service manager." >&2
    exit 1
fi

cd "$PROJECT_DIR"

echo "Installing project dependencies..."
uv sync

if [[ ! -f "$VOICE.onnx" || ! -f "$VOICE.onnx.json" ]]; then
    echo "Downloading voice: $VOICE"
    uv run python -m piper.download_voices "$VOICE"
else
    echo "Voice already present: $VOICE"
fi

echo "Installing the tts command..."
uv tool install --editable . --force

TTS_BIN="$(uv tool dir --bin)/tts"

if [[ ! -x "$TTS_BIN" ]]; then
    printf 'Installed command not found: %s\n' "$TTS_BIN" >&2
    exit 1
fi

# The generated unit uses unquoted systemd paths. Avoid generating a broken
# service if the home directory contains whitespace or systemd specifiers.
if [[ "$HOME" == *[[:space:]%]* ]]; then
    echo "Home paths containing whitespace or '%' are not supported." >&2
    exit 1
fi

mkdir -p "$SERVICE_DIR"

if [[ -e "$SERVICE_FILE" ]]; then
    printf 'Service file already exists; leaving it unchanged: %s\n' \
        "$SERVICE_FILE"
    echo "Check that its ExecStart points to: $TTS_BIN serve"
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

echo
echo "Installation complete."
echo "Start and test with:"
echo '  tts start'
echo '  tts status'
echo '  tts read "Hello from the local speech queue."'
echo
echo "If tts is not on your PATH, run: uv tool update-shell"
