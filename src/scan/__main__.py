"""Allow ``python -m scan`` invocation."""

import sys

from .cli import main

sys.exit(main())
