import argparse
import asyncio
import json
from pathlib import Path

from .experiment import compare


def main():
    parser = argparse.ArgumentParser(
        description="Online fixed/RTT/EWMA command comparison"
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summaries = asyncio.run(compare(json.loads(args.plan.read_text()), args.output))
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
