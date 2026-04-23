"""CLI wrapper to retrain the intent classifier from seed + history.

    python scripts/fit.py
    python scripts/fit.py --history /data/history.db --out /data/intent.joblib
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import settings  # noqa: E402
from app.training.fit import fit  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--history", default=settings.history_db)
    p.add_argument("--out", default=settings.intent_model_path)
    args = p.parse_args()

    report = fit(args.history, args.out)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
