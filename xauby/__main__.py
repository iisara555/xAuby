"""Allow `python -m xauby`."""

from xauby.cli import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
