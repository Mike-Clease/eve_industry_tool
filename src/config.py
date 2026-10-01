from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # adjust depth to taste
DATA_DIR = PROJECT_ROOT / "data"
EXTERNAL_DIR = DATA_DIR / "external"
SRC_DIR = PROJECT_ROOT / "src"
