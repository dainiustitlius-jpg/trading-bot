import sys

from .cli import main

if __name__ == "__main__":  # Windows starts sweep workers by re-importing this module
    sys.exit(main())
