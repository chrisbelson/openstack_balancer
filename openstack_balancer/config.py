# config.py

"""Configuration settings for OpenStack VM Balancer."""

# Resource allocation ratios
CPU_ALLOCATION_RATIO = 8.0  # 8:1 CPU overcommitment
RAM_ALLOCATION_RATIO = 1.5  # Memory overcommitment ratio

# Thresholds and limits
DEFAULT_THRESHOLD = 1.2  # Nodes with VMs 20% above average are overutilized
DEFAULT_MAX_RESOURCE_RATIO = 0.85  # Target nodes should be below 85% utilized
MIN_MEMORY_MB = 4096  # Minimum memory required for a target node (4GB)

# Required OpenStack environment variables
REQUIRED_ENV_VARS = [
    'OS_AUTH_URL',
    'OS_PROJECT_NAME',
    'OS_USERNAME',
    'OS_PASSWORD'
]

# Logging format
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
