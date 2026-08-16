"""12306 MCP wire schemas and normalized query-only railway models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tripweaver.domain.models import DomainModel, SourceMetadata, TransportLeg


class _RailwayWireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class RailwaySeatWire(_RailwayWireModel):
    seat_name: str
    short: str = ""
    seat_type_code: str = ""
    num: str = ""
    price: Decimal = Field(ge=0)
    discount: Decimal | None = None

    @field_validator("short", "seat_type_code", "num", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        return "" if value is None else value


class RailwayTicketWire(_RailwayWireModel):
    train_no: str
    start_train_code: str
    start_date: str
    start_time: str
    arrive_date: str
    arrive_time: str
    lishi: str
    from_station: str
    to_station: str
    from_station_telecode: str
    to_station_telecode: str
    prices: tuple[RailwaySeatWire, ...] = ()
    dw_flag: tuple[str, ...] = ()


class RailwayTicket(DomainModel):
    """One train plus the cheapest seat whose availability is explicit."""

    id: str
    leg: TransportLeg
    train_code: str
    origin_station: str
    destination_station: str
    depart_at: datetime
    arrive_at: datetime
    seat_name: str
    availability: str
    price_per_person_cny: Decimal = Field(ge=0)
    tags: tuple[str, ...] = ()
    source: SourceMetadata
