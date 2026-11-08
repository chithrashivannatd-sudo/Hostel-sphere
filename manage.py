#!/usr/bin/env python
import os
import sys
from pathlib import Path

# Add the inner HOSTELSPHERE project directory
BASE_DIR = Path(__file__).resolve().parent / "HOSTELSPHERE"
sys.path.insert(0, str(BASE_DIR))
os.chdir(BASE_DIR)

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hostel_sphere.settings")
    os.environ.setdefault("USE_SQLITE", "1")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

