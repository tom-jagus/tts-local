import os
import re
import tomllib
from pathlib import Path


DEFAULT_VOICE = "en_US-hfc_female-medium"

# A voice is identified by a filename stem, not an arbitrary path.
VOICE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def xdg_directory(variable: str, fallback: Path) -> Path:
    """Use an absolute XDG directory, or the standard home-directory fallback."""
    value = os.environ.get(variable)

    if value:
        path = Path(value)
        if path.is_absolute():
            return path

    return fallback


def config_file() -> Path:
    base = xdg_directory(
        "XDG_CONFIG_HOME",
        Path.home() / ".config",
    )
    return base / "tts-local" / "config.toml"


def voices_directory() -> Path:
    base = xdg_directory(
        "XDG_DATA_HOME",
        Path.home() / ".local" / "share",
    )
    return base / "tts-local" / "voices"


def validate_voice_name(name: object) -> str:
    if not isinstance(name, str) or not VOICE_NAME.fullmatch(name):
        raise ValueError(
            "Voice name must contain only letters, numbers, underscores, "
            "and hyphens"
        )

    return name


def read_config() -> dict:
    path = config_file()

    if not path.exists():
        return {}

    try:
        with path.open("rb") as config:
            return tomllib.load(config)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Cannot read configuration {path}: {error}") from error


def default_voice() -> str:
    return validate_voice_name(read_config().get("default_voice", DEFAULT_VOICE))


def inference_threads() -> int:
    value = read_config().get("inference_threads", 2)
    if type(value) is not int or not 1 <= value <= 64:
        raise ValueError("inference_threads must be an integer between 1 and 64")
    return value


def voice_path(name: object) -> Path:
    name = validate_voice_name(name)
    model = voices_directory() / f"{name}.onnx"
    config = voices_directory() / f"{name}.onnx.json"

    if not model.is_file() or not config.is_file():
        raise ValueError(
            f"Voice {name!r} is not installed. Expected {model} "
            f"and {config}"
        )

    return model


def available_voices() -> list[str]:
    directory = voices_directory()

    if not directory.is_dir():
        return []

    voices = []

    for model in directory.glob("*.onnx"):
        name = model.name.removesuffix(".onnx")

        if (
            VOICE_NAME.fullmatch(name)
            and model.is_file()
            and (directory / f"{name}.onnx.json").is_file()
        ):
            voices.append(name)

    return sorted(voices)
