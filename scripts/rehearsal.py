import argparse
from datetime import datetime

from app.clock import LONDON

PHASES = {
    "prediction-open": datetime(2026, 8, 10, 12, tzinfo=LONDON),
    "deadline": datetime(2026, 8, 21, 0, 1, tzinfo=LONDON),
    "swap-1": datetime(2026, 9, 15, 12, tzinfo=LONDON),
    "swap-2": datetime(2026, 11, 15, 12, tzinfo=LONDON),
    "swap-3": datetime(2027, 1, 15, 12, tzinfo=LONDON),
    "swap-4": datetime(2027, 3, 15, 12, tzinfo=LONDON),
    "final": datetime(2027, 5, 1, 12, tzinfo=LONDON),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a deterministic staging rehearsal time")
    parser.add_argument("phase", choices=PHASES)
    args = parser.parse_args()
    print(f"SEASON27_DEV_NOW={PHASES[args.phase].isoformat()}")


if __name__ == "__main__":
    main()
