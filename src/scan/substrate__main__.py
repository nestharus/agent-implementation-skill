"""Allow ``python -m substrate`` invocation."""

import sys

from scan.substrate_runner import main

sys.exit(main())
