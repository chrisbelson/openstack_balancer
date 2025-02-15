# manager.py

"""Main VM balancing functionality."""

import logging
import requests
from typing import List, Tuple, Dict, Any, Optional

from openstack.compute.v2.server import Server
from openstack.connection import Connection

from .migration_planner import MigrationPlanner
from .config import CPU_ALLOCATION_RATIO, RAM_ALLOCATION_RATIO, DEFAULT_MAX_RESOURCE_RATIO
from .exceptions import OpenStackError, ResourceError
from .models import NodeResources
from .utils import get_openstack_connection, calculate_average_vms

logger = logging.getLogger(__name__)

class OpenStackVMManager:
    """Manages VM distribution across OpenStack compute nodes."""

    def __init__(self, dry_run: bool = False):
        """Initialize the manager."""
        self.dry_run = dry_run
        self.conn = get_openstack_connection()
        self.flavor_cache = self.cache_flavors()
        self.host_traits_cache = {}

    def cache_flavors(self) -> dict:
        """Fetch and cache all flavors by ID and name."""
        try:
            flavors = self.conn.compute.flavors()
            flavor_cache = {}
            for flavor in flavors:
                flavor_cache[flavor.id] = flavor
                flavor_cache[flavor.name] = flavor
            logger.debug(f"Cached {len(flavor_cache)} flavors.")
            return flavor_cache
        except Exception as e:
            raise OpenStackError(f"Failed to fetch flavors: {e}")

    def get_flavor(self, flavor_id_or_name: str):
        """Retrieve a flavor from the cache."""
        flavor = self.flavor_cache.get(flavor_id_or_name)
        if not flavor:
            raise OpenStackError(f"Flavor {flavor_id_or_name} not found.")
        return flavor

    def fetch_hypervisor_details(self) -> List[dict]:
        """Fetch detailed hypervisor information using os-hypervisors/detail."""
        try:
            auth_token = self.conn.auth_token
            compute_url = self.conn.endpoint_for("compute")
            response = requests.get(
                f"{compute_url}/os-hypervisors/detail",
                headers={"X-Auth-Token": auth_token},
            )
            response.raise_for_status()
            hypervisors = response.json().get("hypervisors", [])

            # Handle missing attributes specific to Virtuozzo
            for hypervisor in hypervisors:
                hypervisor.setdefault("memory_mb", 0)
                hypervisor.setdefault("memory_mb_used", 0)
                hypervisor.setdefault("vcpus", 0)
                hypervisor.setdefault("vcpus_used", 0)
                hypervisor.setdefault("running_vms", 0)
                hypervisor.setdefault("state", "unknown")
                hypervisor.setdefault("status", "unknown")

            return hypervisors
        except Exception as e:
            raise OpenStackError(f"Failed to fetch hypervisor details: {e}")

    @staticmethod
    def get_node_resources(hypervisor: dict) -> NodeResources:
        """Extract resource details for a compute node."""
        name = hypervisor.get("hypervisor_hostname", "Unknown")
        vcpus = hypervisor.get("vcpus", 0)
        vcpus_used = hypervisor.get("vcpus_used", 0)
        memory_mb = hypervisor.get("memory_mb", 0)
        memory_mb_used = hypervisor.get("memory_mb_used", 0)
        running_vms = hypervisor.get("running_vms", 0)

        # Calculate ratios with overcommit
        cpu_ratio = vcpus_used / (vcpus * CPU_ALLOCATION_RATIO) if vcpus > 0 else 1.0
        memory_ratio = memory_mb_used / (memory_mb * RAM_ALLOCATION_RATIO) if memory_mb > 0 else 1.0

        return NodeResources(
            name=name,
            vcpus=vcpus,
            vcpus_used=vcpus_used,
            memory_mb=memory_mb,
            memory_mb_used=memory_mb_used,
            running_vms=running_vms,
            cpu_ratio=cpu_ratio,
            memory_ratio=memory_ratio,
            status=hypervisor.get("status", "unknown"),
            state=hypervisor.get("state", "unknown"),
        )

    @staticmethod
    def print_node_resources(resources: NodeResources) -> None:
        """Print node resources."""
        logger.info(f"Node {resources.name}:")
        logger.info(f"  CPUs: {resources.vcpus_used}/{resources.vcpus} "
                   f"({resources.cpu_ratio * 100:.1f}%)")
        logger.info(f"  Memory: {resources.memory_mb_used}/{resources.memory_mb}MB "
                   f"({resources.memory_ratio * 100:.1f}%)")
        logger.info(f"  Running VMs: {resources.running_vms}")
        logger.info(f"  Status: {resources.status}, State: {resources.state}")

    def identify_node_groups(
        self,
        hypervisors: List[dict],
        avg_vms: float,
        threshold: float
    ) -> Tuple[List[dict], List[dict]]:
        """Group nodes into overutilized and underutilized."""
        overutilized, underutilized = [], []

        for hypervisor in hypervisors:
            running_vms = hypervisor.get("running_vms", 0)
            # Only consider nodes that are up and enabled
            if (hypervisor.get("state") != "up" or
                hypervisor.get("status") != "enabled"):
                logger.debug(f"Skipping node {hypervisor.get('hypervisor_hostname')} "
                           f"due to state/status")
                continue

            if running_vms > avg_vms * threshold:
                overutilized.append(hypervisor)
            else:
                underutilized.append(hypervisor)

        return overutilized, underutilized

    def _check_trait_compatibility(self, vm_host: str, target_host: str) -> bool:
        """Check if target host has required HCI traits for VMs on source host."""
        try:
            # Get source host's resource provider UUID
            response = requests.get(
                f"{self.conn.endpoint_for('compute')}/os-hypervisors/detail",
                headers={"X-Auth-Token": self.conn.auth_token},
            )
            response.raise_for_status()
    
            # Get VMs from source host
            vms = list(self.conn.compute.servers(all_projects=True, host=vm_host))
    
            # Get trait information for target host
            placement_url = self.conn.endpoint_for('placement')
            providers_response = requests.get(
                f"{placement_url}/resource_providers?name={target_host}",
                headers={
                    "X-Auth-Token": self.conn.auth_token,
                    "OpenStack-API-Version": "placement 1.32"
                }
            )
            providers_response.raise_for_status()
            providers = providers_response.json().get('resource_providers', [])
    
            if not providers:
                logger.error(f"No resource provider found for host {target_host}")
                return False
    
            target_provider_uuid = providers[0]['uuid']
    
            # Get traits for target host
            traits_response = requests.get(
                f"{placement_url}/resource_providers/{target_provider_uuid}/traits",
                headers={
                    "X-Auth-Token": self.conn.auth_token,
                    "OpenStack-API-Version": "placement 1.32"
                }
            )
            traits_response.raise_for_status()
            target_traits = traits_response.json().get('traits', [])
    
            # Check each VM's required traits
            for vm in vms:
                # Get VM details including HCI info
                vm_response = requests.get(
                    f"{self.conn.endpoint_for('compute')}/servers/{vm.id}",
                    headers={"X-Auth-Token": self.conn.auth_token}
                )
                vm_response.raise_for_status()
                vm_data = vm_response.json()['server']
    
                # Get required traits from HCI info
                required_traits = vm_data.get('hci_info', {}).get('required_traits', [])
    
                # Get flavor extra specs for additional traits
                flavor_id = vm_data['flavor']['id']
                if flavor_id in self.flavor_cache:
                    flavor = self.flavor_cache[flavor_id]
                    if hasattr(flavor, 'extra_specs'):
                        for key, value in flavor.extra_specs.items():
                            if key.startswith('trait:') and value.lower() == 'required':
                                trait = key.split(':', 1)[1]
                                required_traits.append(trait)
    
                # Check if target host has all required traits
                missing_traits = set(required_traits) - set(target_traits)
                if missing_traits:
                    logger.warning(f"Host {target_host} missing required traits for VM {vm.name}: {missing_traits}")
                    return False
    
            return True
    
        except Exception as e:
            logger.error(f"Error checking trait compatibility: {e}")
            return False

    def is_target_host_suitable(self, target_node: dict, vm: Server, vm_flavor: Any) -> bool:
        """Enhanced host suitability check including traits and resources."""
        try:
            # First check basic resource availability
            total_vcpus = target_node.get('vcpus', 0)
            vcpus_used = target_node.get('vcpus_used', 0)
            total_ram = target_node.get('memory_mb', 0)
            ram_used = target_node.get('memory_mb_used', 0)
    
            # Calculate available resources with overcommit
            available_vcpus = (total_vcpus * CPU_ALLOCATION_RATIO) - vcpus_used
            available_ram = (total_ram * RAM_ALLOCATION_RATIO) - ram_used
    
            if not (available_vcpus >= vm_flavor.vcpus and available_ram >= vm_flavor.ram):
                logger.debug(
                    f"Insufficient resources on {target_node['hypervisor_hostname']}: "
                    f"Available vCPUs: {available_vcpus:.1f}, Required: {vm_flavor.vcpus}; "
                    f"Available RAM: {available_ram}MB, Required: {vm_flavor.ram}MB"
                )
                return False
    
            # Then check trait compatibility
            current_host = vm.get('OS-EXT-SRV-ATTR:host')
            if not self._check_trait_compatibility(current_host, target_node['hypervisor_hostname']):
                return False
    
            return True
    
        except Exception as e:
            logger.error(f"Error checking host suitability: {e}")
            return False


    def migrate_vm(
        self,
        vm: Server,
        target_node: dict
    ) -> bool:
        """Migrate a VM to target node."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would migrate VM {vm.name} to "
                       f"{target_node['hypervisor_hostname']}")
            return True

        try:
            # Initiate live migration
            self.conn.compute.live_migrate_server(
                vm,
                host=target_node['hypervisor_hostname']
            )
            logger.info(f"Successfully initiated migration for VM {vm.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to migrate VM {vm.name}: {e}")
            return False

    def process_overutilized_nodes(
        self,
        overutilized: List[dict],
        underutilized: List[dict]
    ) -> Tuple[int, int]:
        """Process migrations using optimized cluster-wide planning."""
        migrations_attempted = 0
        migrations_successful = 0
    
        # Initialize planner
        planner = MigrationPlanner(self.conn, self.flavor_cache)
        planner.init_simulation(overutilized + underutilized)
    
        # Get migration plan
        migration_plan = planner.plan_migrations()  # Remove allowed_targets parameter
    
        # Execute migrations
        for vm, source, target_host in migration_plan:
            migrations_attempted += 1
            target = next(h for h in underutilized
                         if h['hypervisor_hostname'] == target_host)
    
            if self.migrate_vm(vm, target):
                migrations_successful += 1
                logger.info(f"Successfully initiated migration: {vm.name} to {target_host}")
            else:
                logger.error(f"Failed to migrate: {vm.name} to {target_host}")
    
        return migrations_attempted, migrations_successful


    def balance_nodes(self, threshold: float) -> None:
        """Main entry point for node balancing."""
        try:
            # Get all compute nodes
            hypervisors = self.fetch_hypervisor_details()
            if not hypervisors:
                logger.error("No compute nodes found")
                return

            # Calculate average VMs per node
            avg_vms = calculate_average_vms(hypervisors)
            logger.info(f"Average VMs per node: {avg_vms:.2f}")

            # Group nodes
            overutilized, underutilized = self.identify_node_groups(
                hypervisors,
                avg_vms,
                threshold
            )

            logger.info(f"Found {len(overutilized)} overutilized and "
                       f"{len(underutilized)} underutilized nodes")

            if not overutilized:
                logger.info("No overutilized nodes found. Cluster is balanced.")
                return

            if not underutilized:
                logger.warning("No underutilized nodes available as migration targets.")
                return

            # Process migrations
            attempted, successful = self.process_overutilized_nodes(
                overutilized,
                underutilized
            )

            # Print summary
            logger.info(f"\nMigration Summary:")
            logger.info(f"Attempted: {attempted}")
            logger.info(f"Successful: {successful}")
            logger.info(f"Failed: {attempted - successful}")

        except Exception as e:
            logger.error(f"Error during node balancing: {e}")
            raise
