"""AMap wire schemas and provenance-bearing normalized models."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, field_validator

from tripweaver.domain.models import DomainModel, GeoPoint, SourceMetadata


class _AmapWireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _PoiSummaryWire(_AmapWireModel):
    id: str
    name: str
    address: str = ""
    typecode: str = ""
    photo: str = ""

    @field_validator("address", "typecode", "photo", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        return "" if value is None or value == [] else value


class AmapPoiSearchWire(_AmapWireModel):
    pois: tuple[_PoiSummaryWire, ...] = ()


class AmapPoiDetailWire(_AmapWireModel):
    id: str
    name: str
    address: str = ""
    location: str
    type: str = ""
    rating: str = ""
    open_time: str = ""
    opentime2: str = ""
    cost: str = ""
    photo: str = ""

    @field_validator(
        "address",
        "type",
        "rating",
        "open_time",
        "opentime2",
        "cost",
        "photo",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        return "" if value is None or value == [] else value


class _ForecastWire(_AmapWireModel):
    date: date
    week: str
    dayweather: str
    nightweather: str
    daytemp_float: float
    nighttemp_float: float
    daywind: str
    nightwind: str
    daypower: str
    nightpower: str


class AmapWeatherWire(_AmapWireModel):
    city: str
    forecasts: tuple[_ForecastWire, ...] = ()


class _GeocodeWire(_AmapWireModel):
    country: str = ""
    province: str = ""
    city: str = ""
    district: str = ""
    adcode: str = ""
    location: str
    level: str = ""

    @field_validator("country", "province", "city", "district", "adcode", "level", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        return "" if value is None or value == [] else value


class AmapGeocodePayloadWire(_AmapWireModel):
    results: tuple[_GeocodeWire, ...] = ()


class _WalkingPathWire(_AmapWireModel):
    distance: int
    duration: int


class _WalkingRouteWire(_AmapWireModel):
    origin: str
    destination: str
    paths: tuple[_WalkingPathWire, ...] = ()


class AmapWalkingPayloadWire(_AmapWireModel):
    route: _WalkingRouteWire


class _TransitOptionWire(_AmapWireModel):
    duration: int
    walking_distance: int = 0


class AmapTransitPayloadWire(_AmapWireModel):
    origin: str
    destination: str
    distance: int
    transits: tuple[_TransitOptionWire, ...] = ()


class AmapPlaceSummary(DomainModel):
    id: str
    name: str
    address: str
    typecode: str
    photo_url: str | None = None
    source: SourceMetadata


class AmapPlaceDetail(DomainModel):
    id: str
    name: str
    address: str
    location: GeoPoint
    category: str
    rating: float | None = None
    opening_hours_text: str | None = None
    average_cost_cny: float | None = None
    photo_url: str | None = None
    source: SourceMetadata


class AmapForecast(DomainModel):
    date: date
    weekday: int
    day_weather: str
    night_weather: str
    day_temperature_c: float
    night_temperature_c: float
    day_wind: str
    night_wind: str
    day_wind_power: str
    night_wind_power: str


class AmapWeather(DomainModel):
    city: str
    forecasts: tuple[AmapForecast, ...]
    source: SourceMetadata


class AmapGeocodeResult(DomainModel):
    country: str
    province: str
    city: str
    district: str
    adcode: str
    level: str
    location: GeoPoint
    source: SourceMetadata


class AmapRoute(DomainModel):
    mode: str
    origin: GeoPoint
    destination: GeoPoint
    distance_meters: int
    duration_seconds: int
    walking_distance_meters: int = 0
    source: SourceMetadata
