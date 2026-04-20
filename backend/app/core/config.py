from pathlib import Path
import tempfile


APP_NAME = "PDF Flow Diff MVP"
APP_VERSION = "0.1.0"
DEFAULT_HEADER_MARGIN = 50.0
DEFAULT_FOOTER_MARGIN = 50.0
REFLOW_MIN_CHARS = 60
REFLOW_MIN_DELTA_Y = 96.0
DEFAULT_TEMP_DIR = Path(tempfile.gettempdir()) / "pdf-flow-diff"
DEFAULT_TEMP_DIR.mkdir(parents=True, exist_ok=True)
