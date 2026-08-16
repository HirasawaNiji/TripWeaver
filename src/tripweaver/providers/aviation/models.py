"""VariFlight MCP wire schemas and normalized live flight offers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tripweaver.domain.models import DomainModel, SourceMetadata, TransportLeg


class _VariflightWireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class VariflightCabinWire(_VariflightWireModel):
    cabinclass: str
    classname: str = ""
    cabincode: str
    seatnum: int = Field(ge=0)
    stprice: Decimal = Field(ge=0)
    price: Decimal = Field(ge=0)
    discount: float | None = None

    @field_validator("classname", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        return "" if value is None else value


class VariflightFlightWire(_VariflightWireModel):
    flightno: str
    depdate: str
    arrdate: str
    flightdeptimeplandate: int = Field(gt=0)
    flightarrtimeplandate: int = Field(gt=0)
    flightdepcode: str
    flightarrcode: str
    flightterminal: str = ""
    flighthterminal: str = ""
    flightcompany: str = ""
    shareflag: int = 0
    shareflightno: str = ""
    stopflag: int = 0
    tax: str = ""
    oilfee: str = ""
    cabins: tuple[VariflightCabinWire, ...] = ()

    @field_validator(
        "flightterminal",
        "flighthterminal",
        "flightcompany",
        "shareflightno",
        "tax",
        "oilfee",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object) -> str:
        if value is None or value == []:
            return ""
        return str(value)


class VariflightPricePayloadWire(_VariflightWireModel):
    code: int
    message: str = ""
    request_id: str = ""
    timestamp: str = ""
    data: tuple[VariflightFlightWire, ...] = ()

    @field_validator("data", mode="before")
    @classmethod
    def normalize_optional_data(cls, value: object) -> object:
        return () if value is None else value


class FlightOffer(DomainModel):
    """One flight plus its cheapest explicitly available cabin."""

    id: str
    leg: TransportLeg
    flight_number: str
    carrier: str
    origin_airport: str
    destination_airport: str
    departure_terminal: str | None = None
    arrival_terminal: str | None = None
    depart_at: datetime
    arrive_at: datetime
    cabin_class: str
    cabin_code: str
    seats_remaining: int = Field(gt=0)
    base_fare_cny: Decimal = Field(gt=0)
    included_fees_cny: Decimal = Field(ge=0)
    fees_complete: bool
    price_per_person_cny: Decimal = Field(gt=0)
    is_codeshare: bool = False
    operating_flight_number: str | None = None
    has_stop: bool = False
    source: SourceMetadata
