"""Redaction-safe research errors shared by REST and Agent Bridge."""


class ResearchError(RuntimeError):
    """Base class for errors that are safe to show to the local user."""


class ResearchValidationError(ResearchError):
    """The request violates a validation, privacy, or budget boundary."""


class ResearchConfigurationError(ResearchError):
    """A required local provider credential is not configured."""


class ResearchProviderError(ResearchError):
    """The external research provider failed without exposing its response."""
