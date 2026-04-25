import os
from pathlib import Path
import tempfile


APP_NAME = "PDF Flow Diff MVP"
APP_VERSION = "0.1.0"
DEFAULT_HEADER_MARGIN = 50.0
DEFAULT_FOOTER_MARGIN = 50.0
REFLOW_MIN_CHARS = 60
REFLOW_MIN_DELTA_Y = 96.0
MAX_PDF_UPLOAD_BYTES = 50 * 1024 * 1024
TERMINAL_RECORD_TTL_SECONDS = 60 * 60


def _read_bool_env(name: str, *, default: bool) -> bool:
	raw = os.getenv(name)
	if raw is None:
		return default
	return raw.strip().lower() in {"1", "true", "yes", "on"}


PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT = _read_bool_env(
	"PDF_FLOW_DIFF_ENABLE_CHAPTER_SPLIT",
	default=True,
)

DEFAULT_TEMP_DIR = Path(tempfile.gettempdir()) / "pdf-flow-diff"


def ensure_default_temp_dir() -> Path:
	DEFAULT_TEMP_DIR.mkdir(parents=True, exist_ok=True)
	return DEFAULT_TEMP_DIR
