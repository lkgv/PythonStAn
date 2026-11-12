"""Configuration-boundary failures, independent of results and orchestration."""


class ConfigurationError(ValueError):
    """A public request contains an invalid or unsupported configuration."""
