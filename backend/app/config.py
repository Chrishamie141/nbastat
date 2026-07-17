"""FastAPI configuration that loads root .env before service imports."""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import data_mode, get_config_status, print_config_status  # noqa: E402
