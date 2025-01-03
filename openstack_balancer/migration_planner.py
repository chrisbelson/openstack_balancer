# migration_planner.py

"""Migration planning and simulation functionality."""

import logging
import requests
from dataclasses import dataclass
from typing import Dict, Set, List, Tuple, Optional, Any
import copy

from openstack.compute.v2.server import Server

from .config import CPU_ALLOCATION_RATIO, RAM_ALLOCATION_RATIO
from .exceptions import OpenStackError

logger = logging.getLogger(__name__)

@dataclass
class SimulatedState:
    """Tracks simulated node state during migration planning."""
    vcpus_total: int
    vcpus_used: int
    memory_mb_total: int
    memory_mb_used: int
    running_vms: int
    planned_migrations_in: Set[str]  # VM IDs
    planned_migrations_out: Set[str]  # VM IDs

    @property
    def available_vcpus(self) -> float:
        return (self.vcpus_total * CPU_ALLOCATION_RATIO) - self.vcpus_used

    @property
    def available_memory(self) -> float:
        return (self.memory_mb_total * RAM_ALLOCATION_RATIO) - self.memory_mb_used

    @property
    def cpu_ratio(self) -> float:
        return self.vcpus_used / (self.vcpus_total * CPU_ALLOCATION_RATIO) if self.vcpus_total > 0 else 1.0

    @property
    def memory_ratio(self) -> float:
        return self.memory_mb_used / (self.memory_mb_total * RAM_ALLOCATION_RATIO) if self.memory_mb_total > 0 else 1.0

class MigrationPlanner:
    def __init__(self, conn, flavor_cache: Dict):
        self.conn = conn
        self.flavor_cache = flavor_cache
        self.simulated_states: Dict[str, SimulatedState] = {}
        self.provider_uuid_cache = {}
        self.host_traits_cache = {}
        self.migrations_planned = []

    def init_simulation(self, hypervisors: List[dict]) -> None:
        """Initialize simulation state from hypervisors."""
        self.simulated_states.clear()
        for hypervisor in hypervisors:
            hostname = hypervisor['hypervisor_hostname']
            self.simulated_states[hostname] = SimulatedState(
                vcpus_total=hypervisor.get('vcpus', 0),
                vcpus_used=hypervisor.get('vcpus_used', 0),
                memory_mb_total=hypervisor.get('memory_mb', 0),
                memory_mb_used=hypervisor.get('memory_mb_used', 0),
                running_vms=hypervisor.get('running_vms', 0),
                planned_migrations_in=set(),
                planned_migrations_out=set()
            )

    def get_host_traits(self, hostname: str) -> Set[str]:
        """Get and cache traits for a host."""
        if hostname in self.host_traits_cache:
            return self.host_traits_cache[hostname]

        try:
            # Get provider UUID
            if hostname not in self.provider_uuid_cache:
                placement_url = self.conn.endpoint_for('placement')
                response = requests.get(
                    f"{placement_url}/resource_providers?name={hostname}",
                    headers={
                        "X-Auth-Token": self.conn.auth_token,
                        "OpenStack-API-Version": "placement 1.32"
                    }
                )
                response.raise_for_status()
                providers = response.json().get('resource_providers', [])

                if not providers:
                    logger.warning(f"No resource provider found for host {hostname}")
                    return set()

                self.provider_uuid_cache[hostname] = providers[0]['uuid']

            # Get traits
            provider_uuid = self.provider_uuid_cache[hostname]
            placement_url = self.conn.endpoint_for('placement')
            response = requests.get(
                f"{placement_url}/resource_providers/{provider_uuid}/traits",
                headers={
                    "X-Auth-Token": self.conn.auth_token,
                    "OpenStack-API-Version": "placement 1.32"
                }
            )
            response.raise_for_status()
            traits = set(response.json().get('traits', []))
            self.host_traits_cache[hostname] = traits
            return traits

        except Exception as e:
            logger.error(f"Error getting traits for host {hostname}: {e}")
            return set()

    def get_required_traits(self, vm: Server) -> Set[str]:
        """Get required traits for a VM from both HCI info and flavor."""
        required_traits = set()
        try:
            # Get VM details including HCI info
            compute_url = self.conn.endpoint_for('compute')
            response = requests.get(
                f"{compute_url}/servers/{vm.id}",
                headers={"X-Auth-Token": self.conn.auth_token}
            )
            response.raise_for_status()
            vm_data = response.json()['server']

            # Get traits from HCI info
            hci_traits = vm_data.get('hci_info', {}).get('required_traits', [])
            required_traits.update(hci_traits)

            # Get traits from flavor extra specs
            flavor_id = vm_data['flavor']['id']
            if flavor_id in self.flavor_cache:
                flavor = self.flavor_cache[flavor_id]
                if hasattr(flavor, 'extra_specs'):
                    for key, value in flavor.extra_specs.items():
                        if key.startswith('trait:') and value.lower() == 'required':
                            required_traits.add(key.split(':', 1)[1])

            return required_traits

        except Exception as e:
            logger.error(f"Error getting required traits for VM {vm.id}: {e}")
            return set()

    def check_trait_compatibility(self, vm: Server, target_host: str) -> bool:
        """Check if target host has required traits for VM."""
        try:
            required_traits = self.get_required_traits(vm)
            if not required_traits:
                return True  # No required traits

            host_traits = self.get_host_traits(target_host)
            missing_traits = required_traits - host_traits

            if missing_traits:
                logger.debug(f"Host {target_host} missing required traits: {missing_traits}")
                return False

            return True

        except Exception as e:
            logger.error(f"Error checking trait compatibility: {e}")
            return False

    def calculate_node_utilization(self, state: SimulatedState) -> float:
        """Calculate combined utilization score for a node."""
        return max(state.cpu_ratio, state.memory_ratio)

    def calculate_cluster_metrics(self) -> Tuple[float, float, float]:
        """Calculate cluster-wide utilization metrics."""
        if not self.simulated_states:
            return 0.0, 0.0, 0.0

        utilizations = [self.calculate_node_utilization(state)
                       for state in self.simulated_states.values()]

        avg_util = sum(utilizations) / len(utilizations)
        min_util = min(utilizations)
        max_util = max(utilizations)

        return avg_util, min_util, max_util

    def get_migration_candidates(self) -> List[Tuple[Server, str, Any]]:
        """Get sorted list of VMs that are candidates for migration."""
        candidates = []

        # Get VMs from highly utilized nodes
        avg_util, min_util, max_util = self.calculate_cluster_metrics()
        target_util = avg_util * 0.9  # Target slightly below average

        for hostname, state in self.simulated_states.items():
            if self.calculate_node_utilization(state) > target_util:
                try:
                    vms = list(self.conn.compute.servers(
                        all_projects=True,
                        host=hostname
                    ))

                    for vm in vms:
                        if vm.status.upper() == 'ACTIVE':
                            try:
                                flavor = self.flavor_cache[vm.flavor['id']]
                                candidates.append((vm, hostname, flavor))
                            except Exception as e:
                                logger.error(f"Error getting flavor for VM {vm.name}: {e}")
                                continue

                except Exception as e:
                    logger.error(f"Error getting VMs for node {hostname}: {e}")
                    continue

        # Sort by resource footprint (largest first)
        candidates.sort(
            key=lambda x: x[2].vcpus * x[2].ram,
            reverse=True
        )

        return candidates

    def get_best_target(self, vm: Server, source: str, vm_flavor: Any) -> Optional[str]:
        """Find best target node for a VM."""
        avg_util, min_util, max_util = self.calculate_cluster_metrics()
        target_util = avg_util * 0.9  # Target slightly below average
        underutilized_hosts = [
            hostname for hostname, state in self.simulated_states.items()
            if self.calculate_node_utilization(state) <= target_util
        ]
    
        best_target = None
        best_score = float('inf')
    
        # Calculate resource requirements
        required_vcpus = vm_flavor.vcpus
        required_memory = vm_flavor.ram
    
        # Only consider underutilized hosts as potential targets
        for hostname in underutilized_hosts:
            state = self.simulated_states[hostname]
            # Skip source node and nodes with insufficient resources
            if (hostname == source or
                state.available_vcpus < required_vcpus or
                state.available_memory < required_memory):
                continue
    
            # Check trait compatibility
            if not self.check_trait_compatibility(vm, hostname):
                continue
    
            # Calculate post-migration utilization
            new_target_vcpus = state.vcpus_used + required_vcpus
            new_target_memory = state.memory_mb_used + required_memory
    
            new_target_cpu_ratio = new_target_vcpus / (state.vcpus_total * CPU_ALLOCATION_RATIO)
            new_target_mem_ratio = new_target_memory / (state.memory_mb_total * RAM_ALLOCATION_RATIO)
            new_target_util = max(new_target_cpu_ratio, new_target_mem_ratio)
    
            # Score based on how close to target utilization it would be
            score = abs(new_target_util - target_util)
    
            if score < best_score:
                best_score = score
                best_target = hostname
    
        return best_target

    def register_migration(self, vm: Server, source: str, target: str, vm_flavor: Any) -> None:
        """Register a planned migration in the simulation."""
        try:
            # Update source node
            source_state = self.simulated_states[source]
            source_state.vcpus_used -= vm_flavor.vcpus
            source_state.memory_mb_used -= vm_flavor.ram
            source_state.running_vms -= 1
            source_state.planned_migrations_out.add(vm.id)

            # Update target node
            target_state = self.simulated_states[target]
            target_state.vcpus_used += vm_flavor.vcpus
            target_state.memory_mb_used += vm_flavor.ram
            target_state.running_vms += 1
            target_state.planned_migrations_in.add(vm.id)

        except Exception as e:
            logger.error(f"Error registering migration: {e}")

    def plan_migrations(self) -> List[Tuple[Server, str, str]]:
        """Plan migrations for optimal cluster balance."""
        candidates = self.get_migration_candidates()
        planned_migrations = []

        logger.info("\nPlanning migrations:")
        logger.info(f"Found {len(candidates)} migration candidates")

        avg_util, min_util, max_util = self.calculate_cluster_metrics()
        target_util = avg_util * 0.9
        underutilized_hosts = [
                hostname for hostname, state in self.simulated_states.items()
                if self.calculate_node_utilization(state) <= target_util
        ]

        logger.info(f"Current cluster utilization: {avg_util*100:.1f}% (min: {min_util*100:.1f}%, max: {max_util*100:.1f}%)")

        for vm, source, flavor in candidates:
            best_target = self.get_best_target(vm, source, flavor) 

            if best_target:
                # Simulate this migration
                self.register_migration(vm, source, best_target, flavor)
                planned_migrations.append((vm, source, best_target))

                logger.info(f"Planned: {vm.name} from {source} to {best_target}")
                logger.debug(f"  Resources: {flavor.vcpus} vCPUs, {flavor.ram}MB RAM")

                # Log updated utilization
                source_util = self.calculate_node_utilization(self.simulated_states[source])
                target_util = self.calculate_node_utilization(self.simulated_states[best_target])
                logger.debug(f"  Source node utilization: {source_util*100:.1f}%")
                logger.debug(f"  Target node utilization: {target_util*100:.1f}%")
            else:
                logger.debug(f"No suitable target found for VM {vm.name}")

        # Log final cluster state
        if planned_migrations:
            final_avg, final_min, final_max = self.calculate_cluster_metrics()
            logger.info("\nFinal cluster state after planned migrations:")
            logger.info(f"Utilization: {final_avg*100:.1f}% (min: {final_min*100:.1f}%, max: {final_max*100:.1f}%)")

            for hostname, state in self.simulated_states.items():
                util = self.calculate_node_utilization(state)
                if len(state.planned_migrations_in) > 0 or len(state.planned_migrations_out) > 0:
                    logger.info(f"\nNode {hostname}:")
                    logger.info(f"  Utilization: {util*100:.1f}%")
                    logger.info(f"  Migrations in:  {len(state.planned_migrations_in)}")
                    logger.info(f"  Migrations out: {len(state.planned_migrations_out)}")

        return planned_migrations
