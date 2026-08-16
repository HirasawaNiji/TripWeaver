"""Shared city registry used by providers, fixtures, and request normalization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CityInfo:
    name: str
    iata_code: str
    amap_adcode: str
    latitude: float
    longitude: float
    aliases: tuple[str, ...] = ()


CITY_REGISTRY: tuple[CityInfo, ...] = (
    CityInfo("北京", "BJS", "110000", 39.9042, 116.4074, ("北京市",)),
    CityInfo("上海", "SHA", "310000", 31.2304, 121.4737, ("上海市",)),
    CityInfo("广州", "CAN", "440100", 23.1291, 113.2644, ("广州市",)),
    CityInfo("深圳", "SZX", "440300", 22.5431, 114.0579, ("深圳市",)),
    CityInfo("杭州", "HGH", "330100", 30.2741, 120.1551, ("杭州市",)),
    CityInfo("南京", "NKG", "320100", 32.0603, 118.7969, ("南京市",)),
    CityInfo("成都", "CTU", "510100", 30.5728, 104.0668, ("成都市",)),
    CityInfo("重庆", "CKG", "500000", 29.5630, 106.5516, ("重庆市",)),
    CityInfo("西安", "SIA", "610100", 34.3416, 108.9398, ("西安市",)),
    CityInfo("武汉", "WUH", "420100", 30.5928, 114.3055, ("武汉市",)),
)

_BY_NAME = {alias: city for city in CITY_REGISTRY for alias in (city.name, *city.aliases)}


def city_info(value: str) -> CityInfo | None:
    return _BY_NAME.get(value.strip())


def canonical_city_name(value: str) -> str:
    normalized = value.strip()
    info = city_info(normalized)
    return info.name if info is not None else normalized


def supported_city_names() -> tuple[str, ...]:
    return tuple(city.name for city in CITY_REGISTRY)
