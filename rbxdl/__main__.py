"""Allow `python -m rbxdl`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
