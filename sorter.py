#!/usr/bin/env python3
"""Lanceur autonome : `python3 sorter.py`."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sorter.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
