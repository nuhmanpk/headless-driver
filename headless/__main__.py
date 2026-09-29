"""``python -m headless`` — the same as the ``headless-driver`` command."""

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
