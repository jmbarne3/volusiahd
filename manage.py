#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys
from pathlib import Path

# Application code lives under src/ rather than at the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "volusiahd.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
