from pathlib import Path
import sys
import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def isolated_user_settings(tmp_path, monkeypatch):
    """No test or child CLI may use the user's real registry or UI state."""
    for kind in ("CONFIG", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{kind}_HOME", str(tmp_path / f"xdg-{kind.lower()}"))
