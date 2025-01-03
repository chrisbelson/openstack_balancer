# exceptions.py

"""Custom exceptions for OpenStack VM Balancer."""

class OpenStackError(Exception):
    """Base exception for OpenStack-related errors."""
    pass

class ResourceError(OpenStackError):
    """Exception for resource-related errors."""
    pass

class MigrationError(OpenStackError):
    """Exception for migration-related errors."""
    pass

class ConfigurationError(OpenStackError):
    """Exception for configuration-related errors."""
    pass
