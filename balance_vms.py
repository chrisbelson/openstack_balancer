#!/usr/bin/env python3

"""
OpenStack VM Balancer runner script.
Allows direct execution without installation.
"""

import sys
import os

# Add the package directory to Python path
current_dir = os.path.abspath(os.path.dirname(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

try:
    from openstack_balancer.cli import main
except ImportError as e:
    print(f"Error importing package: {e}")
    print(f"Python path: {sys.path}")
    sys.exit(1)

if __name__ == "__main__":
    sys.exit(main())
