#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
EXPECTED_DIR="$HOME/src/tts-local"

CONNECTOR_DIR="$PROJECT_DIR/pi-tts-connector"
CONNECTOR_FILE="$CONNECTOR_DIR/index.ts"

VOICE="en_US-hfc_female-medium"
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

for command in uv aplay systemctl pi npm; do
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
    "$CONNECTOR_FILE" \
    "$CONNECTOR_DIR/package.json" \
    "$CONNECTOR_DIR/package-lock.json"
do
    if [[ ! -f "$file" ]]; then
        printf 'Required connector file not found: %s\n' "$file" >&2
        exit 1
    fi
done

cd "$PROJECT_DIR"

echo "Checking Python dependencies..."
uv sync

if [[ -f "$VOICE.onnx" && -f "$VOICE.onnx.json" ]]; then
    echo "Voice already present: $VOICE"
else
    echo "Downloading voice: $VOICE"
    uv run python -m piper.download_voices "$VOICE"
fi

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

echo "Checking Pi connector dependencies..."

if (
    cd "$CONNECTOR_DIR"
    npm ls --all --omit=dev >/dev/null 2>&1
); then
    echo "Pi connector dependencies are already installed."
else
    echo "Installing Pi connector dependencies from package-lock.json..."
    (
        cd "$CONNECTOR_DIR"
        npm ci
    )
fi

# Pi stores personal package paths relative to its settings file. Check both
# string and object package entries, plus direct extension paths.
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
