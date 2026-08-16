"""Public VariFlight query-only provider API."""

from tripweaver.providers.aviation.models import FlightOffer
from tripweaver.providers.aviation.provider import (
    CITY_IATA_CODES,
    VARIFLIGHT_REQUIRED_TOOLS,
    VARIFLIGHT_SERVER_NAME,
    UnsupportedAviationCityError,
    VariflightCapabilityError,
    VariflightProvider,
    VariflightProviderError,
)

__all__ = [
    "CITY_IATA_CODES",
    "VARIFLIGHT_REQUIRED_TOOLS",
    "VARIFLIGHT_SERVER_NAME",
    "FlightOffer",
    "UnsupportedAviationCityError",
    "VariflightCapabilityError",
    "VariflightProvider",
    "VariflightProviderError",
]
