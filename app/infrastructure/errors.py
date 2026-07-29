"""Sanitized errors raised by the remote monitoring layer."""


class InfrastructureError(Exception):
    """Base error with a stable public error code."""

    code = "connection_failed"


class HostsConfigurationError(InfrastructureError):
    code = "configuration_invalid"


class UnknownHostError(InfrastructureError):
    code = "unknown_host"


class UnknownHostKeyError(InfrastructureError):
    code = "unknown_host_key"


class ChangedHostKeyError(InfrastructureError):
    code = "changed_host_key"


class AuthenticationFailedError(InfrastructureError):
    code = "authentication_failed"


class ConnectionFailedError(InfrastructureError):
    code = "connection_failed"


class CommandTimeoutError(InfrastructureError):
    code = "command_timeout"


class CommandFailedError(InfrastructureError):
    code = "command_failed"


class OutputTooLargeError(InfrastructureError):
    code = "output_too_large"


class ServiceNotAllowedError(InfrastructureError):
    code = "service_not_allowed"
