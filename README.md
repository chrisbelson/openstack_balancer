# OpenStack VM Balancer

A tool for balancing VM load across OpenStack compute nodes, optimized for Virtuozzo environments.

## Features

- Balances VMs across compute nodes based on resource utilization
- Handles CPU overcommitment (8:1 ratio)
- Supports live migration
- Provides dry-run capability
- Shows detailed resource information

## Installation

```bash
git clone <repository-url>
cd openstack-vm-balancer
pip install -e .
```

## Usage

First, make sure your OpenStack environment variables are set:

```bash
export OS_AUTH_URL="https://your-openstack-auth-url"
export OS_PROJECT_NAME="your-project"
export OS_USERNAME="your-username"
export OS_PASSWORD="your-password"
```

Then you can use the tool:

```bash
# Show current resource usage
balance-vms --show-resources

# Do a dry run with verbose output
balance-vms --dry-run --verbose

# Actually perform migrations
balance-vms

# Use custom threshold
balance-vms --threshold 1.5
```

## Options

- `--dry-run`: Simulate migrations without performing them
- `--verbose`: Show detailed logging
- `--show-resources`: Display current resource usage for all nodes
- `--threshold`: Set custom threshold for overutilization (default: 1.2)

## Contributing

Contributions are welcome! Please feel free to submit pull requests.
