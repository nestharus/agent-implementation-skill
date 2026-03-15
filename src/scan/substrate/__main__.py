"""Allow ``python -m scan.substrate`` invocation."""

import sys

from .substrate_discoverer import main

sys.exit(main())
