# cli.py

"""Command-line interface for OpenStack VM Balancer."""

import argparse
import logging
import sys

from .config import DEFAULT_THRESHOLD
from .exceptions import OpenStackError
from .manager import OpenStackVMManager
from .utils import setup_logging

logger = logging.getLogger(__name__)

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Balance VM load across OpenStack compute nodes"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migrations without performing them"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "--show-resources",
        action="store_true",
        help="Show resources for all nodes"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Threshold for overutilized nodes (default: {DEFAULT_THRESHOLD})"
    )
    return parser.parse_args()

def main():
    """Main entry point."""
    args = parse_args()
    setup_logging(args.verbose)

    try:
        manager = OpenStackVMManager(dry_run=args.dry_run)

        if args.show_resources:
            # Show resources for all nodes
            hypervisors = manager.fetch_hypervisor_details()
            logger.info("\nCurrent node resources:")
            for hypervisor in hypervisors:
                resources = manager.get_node_resources(hypervisor)
                manager.print_node_resources(resources)
                logger.info("")
            return 0

        # Perform balancing
        manager.balance_nodes(args.threshold)
        return 0

    except OpenStackError as e:
        logger.error(f"OpenStack error: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
