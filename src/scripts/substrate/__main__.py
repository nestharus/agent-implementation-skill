"""Allow ``python -m substrate`` invocation."""

import sys

from .runner import main

sys.exit(main())
