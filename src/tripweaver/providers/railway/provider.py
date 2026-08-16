"""Query-only adapter for the community 12306 MCP server."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import TypeAdapter

from tripweaver.config import RailwaySettings
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
from tripweaver.providers.railway.models import (
    RailwaySeatWire,
    RailwayTicket,
    RailwayTicketWire,
)

RAILWAY_SERVER_NAME = "railway_12306"
RAILWAY_REQUIRED_TOOLS = frozenset({"get-tickets"})
_TICKET_LIST_ADAPTER = TypeAdapter(tuple[RailwayTicketWire, ...])


class RailwayProviderError(RuntimeError):
    """Safe base error for railway normalization and capability failures."""


class RailwayCapabilityError(RailwayProviderError):
    pass


class RailwayProvider:
    """Typed, read-only facade over Joooook/12306-mcp."""

    def __init__(
        self,
        gateway: McpGateway,
        server_name: str = RAILWAY_SERVER_NAME,
        *,
        candidate_limit: int = 20,
    ) -> None:
        self._gateway = gateway
        self._server_name = server_name
        self._adapter = McpAdapter(gateway, server_name)
        self._candidate_limit = candidate_limit

    @classmethod
    def from_settings(cls, settings: RailwaySettings) -> RailwayProvider:
        if not settings.enabled:
            raise ValueError("railway MCP is disabled")
        config = ServerConfig(
            name=RAILWAY_SERVER_NAME,
            transport=McpTransport.STDIO,
            command=settings.command,
            args=settings.args,
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
        missing = sorted(RAILWAY_REQUIRED_TOOLS - names)
        if missing:
            raise RailwayCapabilityError(
                "12306 MCP is missing required tools: " + ", ".join(missing)
            )
        return tools

    async def health_check(self) -> ServerHealth:
        return await self._gateway.health_check(self._server_name)

    def health(self) -> ServerHealth:
        return self._gateway.health(self._server_name)

    async def search_tickets(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        leg: TransportLeg,
        *,
        limit: int | None = None,
    ) -> tuple[RailwayTicket, ...]:
        if not origin.strip() or not destination.strip():
            raise ValueError("origin and destination must not be empty")
        result_limit = self._candidate_limit if limit is None else limit
        if not 1 <= result_limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        response = await self._adapter.call_and_validate(
            "get-tickets",
            {
                "date": travel_date.isoformat(),
                "fromStation": origin.strip(),
                "toStation": destination.strip(),
                "trainFilterFlags": "",
                "earliestStartTime": 0,
                "latestStartTime": 24,
                "sortFlag": "duration",
                "sortReverse": False,
                "limitedNum": result_limit,
                "format": "json",
            },
            _TICKET_LIST_ADAPTER,
            ttl=timedelta(minutes=2),
            confidence=0.9,
            allow_text_json=True,
        )
        normalized: list[RailwayTicket] = []
        for item in response.data:
            seat = _cheapest_available_seat(item.prices)
            if seat is None:
                continue
            try:
                depart_at = datetime.fromisoformat(f"{item.start_date}T{item.start_time}")
                arrive_at = datetime.fromisoformat(f"{item.arrive_date}T{item.arrive_time}")
            except ValueError as error:
                raise RailwayProviderError("12306 MCP returned an invalid train time") from error
            if arrive_at <= depart_at:
                raise RailwayProviderError("12306 MCP returned a non-positive journey duration")
            ticket_id = _ticket_id(leg, travel_date, item.start_train_code)
            normalized.append(
                RailwayTicket(
                    id=ticket_id,
                    leg=leg,
                    train_code=item.start_train_code,
                    origin_station=item.from_station,
                    destination_station=item.to_station,
                    depart_at=depart_at,
                    arrive_at=arrive_at,
                    seat_name=seat.seat_name,
                    availability=_availability_label(seat.num),
                    price_per_person_cny=seat.price,
                    tags=item.dw_flag,
                    source=response.source.model_copy(
                        update={
                            "source_reference": (
                                response.source.source_reference
                                + f"#train={item.start_train_code};seat={seat.seat_type_code}"
                            )
                        }
                    ),
                )
            )
        return tuple(sorted(normalized, key=lambda item: (item.depart_at, item.id)))

    async def transport_options(
        self,
        origin: str,
        destination: str,
        travel_date: date,
        leg: TransportLeg,
        *,
        limit: int | None = None,
    ) -> tuple[TransportOption, ...]:
        tickets = await self.search_tickets(
            origin,
            destination,
            travel_date,
            leg,
            limit=limit,
        )
        return tuple(
            TransportOption(
                id=ticket.id,
                leg=ticket.leg,
                mode=TransportMode.RAIL,
                label=(
                    f"{ticket.train_code} {ticket.origin_station}→{ticket.destination_station} "
                    f"{ticket.seat_name}（{ticket.availability}）"
                ),
                origin=origin,
                destination=destination,
                depart_at=ticket.depart_at,
                arrive_at=ticket.arrive_at,
                price_per_person_cny=ticket.price_per_person_cny,
                source=ticket.source,
            )
            for ticket in tickets
        )


def _cheapest_available_seat(
    prices: tuple[RailwaySeatWire, ...],
) -> RailwaySeatWire | None:
    available = [seat for seat in prices if seat.price > Decimal(0) and _is_available(seat.num)]
    if not available:
        return None
    return min(available, key=lambda seat: (seat.price, seat.seat_name, seat.seat_type_code))


def _is_available(value: str) -> bool:
    normalized = value.strip()
    if normalized in {"有", "充足"}:
        return True
    return normalized.isdigit() and int(normalized) > 0


def _availability_label(value: str) -> str:
    normalized = value.strip()
    if normalized.isdigit():
        return f"余{int(normalized)}张"
    return "有票"


def _ticket_id(leg: TransportLeg, travel_date: date, train_code: str) -> str:
    safe_code = re.sub(r"[^0-9A-Za-z]", "", train_code).lower()
    return f"rail-{leg.value.lower()}-{travel_date.isoformat()}-{safe_code}"
