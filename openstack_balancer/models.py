# models.py

"""Data models for OpenStack VM Balancer."""

from typing import NamedTuple

class NodeResources(NamedTuple):
    """Resources for a compute node."""
    name: str
    vcpus: int
    vcpus_used: int
    memory_mb: int
    memory_mb_used: int
    running_vms: int
    cpu_ratio: float
    memory_ratio: float
    status: str
    state: str
