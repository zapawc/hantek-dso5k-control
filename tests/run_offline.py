"""Run the local regression suite with unittest progress on stdout."""
from pathlib import Path
import sys
import unittest


def main():
    here = str(Path(__file__).resolve().parent)
    suite = unittest.defaultTestLoader.discover(here)
    if not suite.countTestCases():
        print("No offline tests discovered.", file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
