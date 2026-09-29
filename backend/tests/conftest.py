from pathlib import Path

WORK = Path(__file__).resolve().parents[2] / ".agentatlas" / "work"
WORK.mkdir(parents=True, exist_ok=True)
