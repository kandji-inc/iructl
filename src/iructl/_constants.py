import os
import re
from pathlib import Path

import platformdirs

APP_NAME = os.environ.get("_IRUCTL_APP_NAME", "iructl")
APP_BRANDING = os.environ.get("_IRUCTL_APP_BRANDING", "Iru Control")
ENV_PREFIX = os.environ.get("IRUCTL_ENV_PREFIX", "IRUCTL")

if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", APP_NAME):
    raise ValueError(
        f"invalid value for environment variable _IRUCTL_APP_NAME: {APP_NAME!r} "
        "(must start with a letter and contain only letters, digits, underscores, or hyphens)"
    )
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ENV_PREFIX):
    raise ValueError(
        f"invalid value for environment variable IRUCTL_ENV_PREFIX: {ENV_PREFIX!r} "
        "(must start with a letter or underscore and contain only letters, digits, or underscores)"
    )
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]*[A-Za-z0-9]", APP_BRANDING):
    raise ValueError(
        f"invalid value for environment variable _IRUCTL_APP_BRANDING: {APP_BRANDING!r} "
        "(must start and end with an alphanumeric and contain only letters, digits, spaces, underscores, or hyphens)"
    )


def _detect_source() -> str:
    """Suffix the app name with -ci when running in a CI environment."""
    if "CI" in os.environ:
        return f"{APP_NAME}-ci"
    return APP_NAME


SOURCE = _detect_source()

TENANT_ENV = f"{ENV_PREFIX}_TENANT"
TOKEN_ENV = f"{ENV_PREFIX}_TOKEN"
PREVIEW_ENV = f"{ENV_PREFIX}_PREVIEW"
PAYLOAD_DIR_ENV = f"{ENV_PREFIX}_PAYLOAD_DIR"
OUTPUT_FORMAT_ENV = f"{ENV_PREFIX}_OUTPUT_FORMAT"
INFO_FORMAT_ENV = f"{ENV_PREFIX}_INFO_FORMAT"
DEBUG_ENV = f"{ENV_PREFIX}_DEBUG"
GIT_ENV = f"{ENV_PREFIX}_GIT_ENABLED"

LOG_DIR = platformdirs.user_log_path(appname=APP_NAME)
LOG_FILE = LOG_DIR / f"{APP_NAME}.log"
REPORT_FILE = LOG_DIR / f"{APP_NAME}_report.json"
ROOT_MARKER = f".{APP_NAME}"
LEGACY_ROOT_MARKER = ".kst"  # pre-rename marker

# boolean check if running in compatibility mode for kst
IS_KST = APP_NAME == "kst"


def _user_config_dir() -> Path:
    """User config directory, honoring XDG_CONFIG_HOME on every platform.

    platformdirs only consults XDG_CONFIG_HOME on Linux; respect it everywhere so an explicit
    override works on macOS too, falling back to the platform default when unset.
    """
    if xdg_config_home := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg_config_home) / APP_NAME
    return platformdirs.user_config_path(appname=APP_NAME)


USER_CONFIG_FILE = _user_config_dir() / "config.yaml"

PROFILES_DIR = "profiles"
SCRIPTS_DIR = "scripts"
APPS_DIR = "apps"
PAYLOADS_DIR = "payloads"

DEFAULT_SCRIPT_CATEGORY = "Utilities"
DEFAULT_APP_CATEGORY = "Apps"
