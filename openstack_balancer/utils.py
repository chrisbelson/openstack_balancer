# utils.py

"""Utility functions for OpenStack VM Balancer."""

import logging
import os
from typing import List, Dict, Any

import openstack
from openstack.connection import Connection

from .config import (
    CPU_ALLOCATION_RATIO, RAM_ALLOCATION_RATIO,
    REQUIRED_ENV_VARS, LOG_FORMAT, LOG_DATE_FORMAT
)
from .exceptions import ConfigurationError, OpenStackError

def setup_logging(verbose: bool = False) -> None:
    """Configure logging with appropriate level and format."""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT
    )

def get_openstack_connection() -> Connection:
    """
    Establish connection to OpenStack using environment variables.
    Raises ConfigurationError if required variables are missing.
    """
    missing_vars = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing_vars:
        raise ConfigurationError(
            f"Missing required environment variables: {', '.join(missing_vars)}"
        )

    try:
        return openstack.connect()
    except Exception as e:
        raise OpenStackError(f"Failed to connect to OpenStack: {e}")

def calculate_average_vms(hypervisors: List[Dict[str, Any]]) -> float:
    """
    Calculate the average number of VMs per active node.

    Args:
        hypervisors: List of hypervisor dictionaries

    Returns:
        Float representing average number of VMs per node
    """
    active_nodes = [
        h for h in hypervisors
        if h.get("state") == "up" and h.get("status") == "enabled"
    ]

    if not active_nodes:
        return 0.0

    total_vms = sum(h.get("running_vms", 0) for h in active_nodes if h.get("running_vms") is not None)
    return total_vms / len(active_nodes)
