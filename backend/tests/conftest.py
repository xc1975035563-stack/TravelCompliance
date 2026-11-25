import os
import tempfile
from pathlib import Path

_db = Path(tempfile.mkdtemp(prefix="tc-pytest-")) / "test.db"
os.environ["DATABASE_URL"] = "sqlite:///" + _db.as_posix()
os.environ["AUTO_SEED"] = "0"
os.environ["JWT_SECRET"] = "DEMO_ONLY_JWT_SECRET_NOT_FOR_PRODUCTION"
os.environ["DEMO_PASSWORD"] = "DemoOnly#2026"
