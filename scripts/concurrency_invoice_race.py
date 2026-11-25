"""重复运行：两线程抢同一张发票，只有一笔成功。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.race_demo import run_invoice_race


def main():
    first = run_invoice_race()
    second = run_invoice_race()
    print(first)
    print(second)
    if not first["passed"] or not second["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
