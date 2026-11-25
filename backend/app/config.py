import os
from pathlib import Path

# 演示默认值。生产环境必须替换，本仓库不提供生产密钥。
JWT_SECRET = os.environ.get("JWT_SECRET", "DEMO_ONLY_JWT_SECRET_NOT_FOR_PRODUCTION")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "DemoOnly#2026")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./travel_demo.db")
AUTO_SEED = os.environ.get("AUTO_SEED", "1") == "1"
TOKEN_HOURS = int(os.environ.get("TOKEN_HOURS", "12"))

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", REPO_ROOT / "frontend"))
SAMPLE_DIR = Path(os.environ.get("SAMPLE_DIR", REPO_ROOT / "data" / "sample"))

COMPANY_NAME = "青禾智造（演示）有限公司"
COMPANY_TAX_NO = "91330100DEMO00000X"
