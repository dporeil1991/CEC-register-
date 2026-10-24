#!/usr/bin/env python3
"""cec - instant lookup against the CEC approved products register.

  cec check inverter "SG5K-D" --job 51814        one product, logged against a job
  cec check panel "RSM108-9-440NDG" --watts 440
  cec check "Fronius Primo 5.0-1" --as-at 14/03/2025
  cec batch jobs.csv --out results.csv            many rows at once
  cec expiring --days 90                          logged products lapsing soon
  cec jobs / cec stats / cec refresh
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ceclib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
