#!/usr/bin/env python3
"""Backwards-compatibility wrapper for writer/fix_oasis_summary.py."""

from __future__ import annotations

import sys

from writer.fix_oasis_summary import *  # noqa: F401, F403
from writer.fix_oasis_summary import main

if __name__ == "__main__":
    sys.exit(main())

