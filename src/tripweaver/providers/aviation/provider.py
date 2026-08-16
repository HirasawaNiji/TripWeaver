"""Strict, query-only provider for the official VariFlight MCP package."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from tripweaver.config import VariflightSettings
from tripweaver.domain.cities import CITY_REGISTRY
from tripweaver.domain.models import TransportLeg, TransportMode, TransportOption
from tripweaver.mcp_gateway import (
    McpAdapter,
    McpGateway,
    McpRegistry,
    McpSdkClientFactory,
    McpTransport,
    ServerConfig,
    ServerHealth,
    ToolDefinition,
)
from tripweaver.providers.aviation.models import (
    FlightOffer,
    VariflightCabinWire,
    VariflightPricePayloadWire,
)

VARIFLIGHT_SERVER_NAME = "variflight"
VARIFLIGHT_REQUIRED_TOOLS = frozenset({"getFlightPriceByCities", "getFutureWeatherByAirport"})
CITY_IATA_CODES = {
    alias: city.iata_code for city in CITY_REGISTRY for alias in (city.name, *city.aliases)
}
_CHINA_TIMEZONE = timezone(timedelta(hours=8))


class VariflightProviderError(RuntimeError):
    """Safe base error for VariFlight schema and upstream failures."""


class VariflightCapabilityError(VariflightProviderError):
    pass


class UnsupportedAviationCityError(VariflightProviderError):
    pass


class VariflightProvider:
    """Typed query-only facade over @variflight-ai/variflight-mcp."""

    def __init__(
        self,
        gateway: McpGateway,
        server_name: str = VARIFLIGHT_SERVER_NAME,
        *,
        candidate_limit: int = 80,
    ) -> None:
        self._gateway = gateway
        self._server_name = server_name
        self._adapter = McpAdapter(gateway, server_name)
        self._candidate_limit = candidate_limit

    @classmethod
    def from_settings(cls, settings: VariflightSettings) -> VariflightProvider:
        if not settings.enabled:
            raise ValueError("VariFlight MCP is disabled")
        if not settings.api_key:
            raise ValueError("VariFlight API key is missing")
        config = ServerConfig(
            name=VARIFLIGHT_SERVER_NAME,
            transport=McpTransport.STDIO,
            command=settings.command,
            args=settings.args,
            env={"VARIFLIGHT_API_KEY": settings.api_key},
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            max_concurrency=settings.max_concurrency,
            health_failure_threshold=2,
        )
        gateway = McpGateway(McpRegistry((config,)), McpSdkClientFactory())
        return cls(gateway, candidate_limit=settings.candidate_limit)

    async def verify_capabilities(self) -> tuple[ToolDefinition, ...]:
        tools = await self._gateway.discover_tools(self._server_name, force_refresh=True)
        names = {tool.name for tool in tools}
        missing = sorted(VARIFLIGHT_REQUIRED_TOOLS - names)
        if missing:
            raise VariflightCapabilityError(
                "VariFlight MCP is missing required tools: " + ", ".join(missing)
            )
        return tools

    async def health_check(self) -> ServerHealth:
        return await self._gateway.health_check(self._server_name)

    def health(self) -> ServerHealth:
        return self._gateway.health(self._server_name)

    async def search_offers(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        leg: TransportLeg,
        *,
        limit: int | None = None,
    ) -> tuple[FlightOffer, ...]:
        result_limit = self._candidate_limit if limit is None else limit
        if not 1 <= result_limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        origin_code = _city_code(origin)
        destination_code = _city_code(destination)
        response = await self._adapter.call_and_validate(
            "getFlightPriceByCities",
            {
                "dep_city": origin_code,
                "arr_city": destination_code,
                "dep_date": travel_date.isoformat(),
            },
            VariflightPricePayloadWire,
            ttl=timedelta(minutes=5),
            confidence=0.9,
            allow_text_json=True,
        )
        if response.data.code != 200:
            raise VariflightProviderError("VariFlight API returned a non-success status")

        offers: dict[str, FlightOffer] = {}
        for item in response.data.data:
            cabin = _cheapest_available_cabin(item.cabins)
            if cabin is None:
                continue
            depart_at = _china_local_time(item.flightdeptimeplandate)
            arrive_at = _china_local_time(item.flightarrtimeplandate)
            if arrive_at <= depart_at or depart_at.date() != travel_date:
                continue
            fees, fees_complete = _fees(item.tax, item.oilfee)
            offer_id = _offer_id(leg, depart_at, item.flightno)
            offer = FlightOffer(
                id=offer_id,
                leg=leg,
                flight_number=item.flightno,
                carrier=item.flightcompany,
                origin_airport=item.flightdepcode,
                destination_airport=item.flightarrcode,
                departure_terminal=item.flightterminal or None,
                arrival_terminal=item.flighthterminal or None,
                depart_at=depart_at,
                arrive_at=arrive_at,
                cabin_class=_cabin_name(cabin.cabinclass),
                cabin_code=cabin.cabincode,
                seats_remaining=cabin.seatnum,
                base_fare_cny=cabin.price,
                included_fees_cny=fees,
                fees_complete=fees_complete,
                price_per_person_cny=cabin.price + fees,
                is_codeshare=bool(item.shareflag),
                operating_flight_number=item.shareflightno or None,
                has_stop=bool(item.stopflag),
                source=response.source.model_copy(
                    update={
                        "source_reference": (
                            response.source.source_reference
                            + f"#flight={item.flightno};cabin={cabin.cabincode}"
                        )
                    }
                ),
            )
            previous = offers.get(offer_id)
            if previous is None or offer.price_per_person_cny < previous.price_per_person_cny:
                offers[offer_id] = offer
        return tuple(
            sorted(
                offers.values(),
                key=lambda item: (item.depart_at, item.price_per_person_cny, item.id),
            )[:result_limit]
        )

    async def transport_options(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        leg: TransportLeg,
        *,
        limit: int | None = None,
    ) -> tuple[TransportOption, ...]:
        offers = await self.search_offers(
            origin,
            destination,
            travel_date,
            leg,
            limit=limit,
        )
        return tuple(
            TransportOption(
                id=offer.id,
                leg=offer.leg,
                mode=TransportMode.FLIGHT,
                label=_offer_label(offer),
                origin=origin,
                destination=destination,
                depart_at=offer.depart_at,
                arrive_at=offer.arrive_at,
                price_per_person_cny=offer.price_per_person_cny,
                source=offer.source,
            )
            for offer in offers
        )


def _city_code(value: str) -> str:
    normalized = value.strip()
    if re.fullmatch(r"[A-Za-z]{3}", normalized):
        return normalized.upper()
    try:
        return CITY_IATA_CODES[normalized]
    except KeyError as error:
        raise UnsupportedAviationCityError(
            "no configured aviation city code for requested city"
        ) from error


def _cheapest_available_cabin(
    cabins: tuple[VariflightCabinWire, ...],
) -> VariflightCabinWire | None:
    available = [cabin for cabin in cabins if cabin.seatnum > 0 and cabin.price > Decimal(0)]
    if not available:
        return None
    return min(available, key=lambda item: (item.price, item.cabinclass, item.cabincode))


def _china_local_time(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=_CHINA_TIMEZONE).replace(tzinfo=None)


def _optional_money(value: str) -> Decimal | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    return amount if amount >= 0 else None


def _fees(tax: str, oilfee: str) -> tuple[Decimal, bool]:
    values = (_optional_money(tax), _optional_money(oilfee))
    return sum((value or Decimal(0) for value in values), start=Decimal(0)), all(
        value is not None for value in values
    )


def _cabin_name(code: str) -> str:
    return {
        "F": "头等舱",
        "C": "公务舱",
        "Y": "经济舱",
    }.get(code.upper(), f"{code.upper()}舱")


def _offer_id(leg: TransportLeg, depart_at: datetime, flight_number: str) -> str:
    safe_number = re.sub(r"[^0-9A-Za-z]", "", flight_number).lower()
    return f"flight-{leg.value.lower()}-{depart_at:%Y-%m-%d-%H%M}-{safe_number}"


def _offer_label(offer: FlightOffer) -> str:
    departure = offer.origin_airport + (
        f" {offer.departure_terminal}" if offer.departure_terminal else ""
    )
    arrival = offer.destination_airport + (
        f" {offer.arrival_terminal}" if offer.arrival_terminal else ""
    )
    return (
        f"{offer.flight_number} {departure}→{arrival} "
        f"{offer.cabin_class}（余{offer.seats_remaining}）"
    )
