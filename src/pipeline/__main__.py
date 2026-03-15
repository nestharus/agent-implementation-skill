"""Allow ``python -m pipeline`` invocation."""

import sys

from pipeline.runner import main

sys.exit(main())
