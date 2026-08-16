"""Portfolio-oriented multi-city fixture seeds; every value is explicitly non-live."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import time
from decimal import Decimal


@dataclass(frozen=True)
class AttractionSeed:
    name: str
    category: str
    latitude: float
    longitude: float
    duration_minutes: int
    admission_cny: Decimal
    opens_at: time
    closes_at: time
    closed_weekdays: tuple[int, ...]
    tags: tuple[str, ...]
    priority: int


def _museum(
    name: str, latitude: float, longitude: float, priority: int, price: int = 0
) -> AttractionSeed:
    return AttractionSeed(
        name,
        "博物馆",
        latitude,
        longitude,
        120,
        Decimal(price),
        time(9),
        time(17),
        (0,),
        ("历史文化",),
        priority,
    )


def _heritage(
    name: str, latitude: float, longitude: float, priority: int, price: int = 40
) -> AttractionSeed:
    return AttractionSeed(
        name,
        "历史景点",
        latitude,
        longitude,
        120,
        Decimal(price),
        time(9),
        time(18),
        (),
        ("历史文化",),
        priority,
    )


def _landmark(
    name: str, latitude: float, longitude: float, priority: int, price: int = 0
) -> AttractionSeed:
    return AttractionSeed(
        name,
        "城市景观",
        latitude,
        longitude,
        90,
        Decimal(price),
        time(8),
        time(22),
        (),
        ("城市景观",),
        priority,
    )


def _district(name: str, latitude: float, longitude: float, priority: int) -> AttractionSeed:
    return AttractionSeed(
        name,
        "特色街区",
        latitude,
        longitude,
        90,
        Decimal(0),
        time(10),
        time(22),
        (),
        ("美食街区", "城市景观"),
        priority,
    )


CITY_ATTRACTIONS: dict[str, tuple[AttractionSeed, ...]] = {
    "北京": (
        _heritage("故宫博物院", 39.9163, 116.3972, 98, 60),
        _museum("中国国家博物馆", 39.9051, 116.4010, 95),
        _heritage("天坛公园", 39.8822, 116.4066, 92, 35),
        _heritage("颐和园", 39.9999, 116.2755, 88, 30),
        _landmark("什刹海", 39.9414, 116.3830, 84),
        _district("南锣鼓巷", 39.9370, 116.4030, 80),
    ),
    "上海": (
        _museum("上海博物馆", 31.2303, 121.4700, 95),
        replace(
            _heritage("豫园", 31.2271, 121.4921, 92, 40),
            tags=("历史文化", "美食街区"),
        ),
        _landmark("外滩", 31.2400, 121.4904, 90),
        _landmark("上海中心观景区", 31.2335, 121.5055, 86, 180),
        replace(_district("田子坊", 31.2101, 121.4687, 78), tags=("美食街区",)),
        _district("南京路步行街", 31.2346, 121.4750, 75),
    ),
    "广州": (
        _museum("广东省博物馆", 23.1190, 113.3212, 96),
        _heritage("陈家祠", 23.1296, 113.2434, 93, 10),
        _landmark("广州塔", 23.1065, 113.3245, 90, 150),
        _heritage("沙面岛", 23.1096, 113.2390, 86, 0),
        _district("北京路步行街", 23.1254, 113.2707, 82),
        _district("永庆坊", 23.1173, 113.2397, 80),
    ),
    "深圳": (
        _museum("深圳博物馆", 22.5485, 114.0579, 95),
        _landmark("世界之窗", 22.5343, 113.9724, 91, 180),
        _landmark("平安金融中心云际观光层", 22.5333, 114.0559, 88, 180),
        _landmark("莲花山公园", 22.5553, 114.0550, 86),
        _district("华侨城创意文化园", 22.5405, 113.9910, 82),
        _district("东门老街", 22.5453, 114.1175, 80),
    ),
    "杭州": (
        _landmark("西湖风景名胜区", 30.2468, 120.1487, 98),
        _museum("良渚博物院", 30.3791, 120.0500, 94),
        _heritage("灵隐寺", 30.2403, 120.1022, 91, 45),
        _heritage("京杭大运河杭州景区", 30.3190, 120.1390, 87),
        _district("河坊街", 30.2384, 120.1715, 83),
        _landmark("西溪国家湿地公园", 30.2708, 120.0633, 80, 80),
    ),
    "南京": (
        _museum("南京博物院", 32.0438, 118.8223, 98),
        _heritage("中山陵", 32.0647, 118.8489, 95),
        _heritage("明孝陵", 32.0586, 118.8350, 92, 70),
        _district("夫子庙秦淮风光带", 32.0206, 118.7888, 88),
        _heritage("南京总统府", 32.0441, 118.7924, 86, 35),
        _landmark("玄武湖公园", 32.0736, 118.7968, 82),
    ),
    "成都": (
        _museum("成都博物馆", 30.6600, 104.0634, 96),
        _heritage("武侯祠", 30.6451, 104.0474, 94, 50),
        _heritage("杜甫草堂", 30.6632, 104.0287, 91, 50),
        _landmark("成都大熊猫繁育研究基地", 30.7380, 104.1467, 90, 55),
        _district("宽窄巷子", 30.6697, 104.0550, 85),
        _district("锦里古街", 30.6454, 104.0471, 82),
    ),
    "重庆": (
        _museum("重庆中国三峡博物馆", 29.5653, 106.5505, 96),
        _landmark("洪崖洞民俗风貌区", 29.5626, 106.5790, 94),
        _landmark("李子坝观景平台", 29.5526, 106.5383, 90),
        _district("解放碑步行街", 29.5570, 106.5770, 88),
        _heritage("磁器口古镇", 29.5810, 106.4500, 84, 0),
        _landmark("重庆长江索道", 29.5569, 106.5873, 82, 30),
    ),
    "西安": (
        _museum("陕西历史博物馆", 34.2250, 108.9531, 98),
        _heritage("秦始皇帝陵博物院", 34.3841, 109.2785, 96, 120),
        _heritage("西安城墙", 34.2594, 108.9470, 92, 54),
        _heritage("大雁塔", 34.2189, 108.9642, 89, 40),
        _landmark("大唐不夜城", 34.2070, 108.9680, 86),
        _district("回民街", 34.2656, 108.9453, 82),
    ),
    "武汉": (
        _museum("湖北省博物馆", 30.5619, 114.3655, 97),
        _heritage("黄鹤楼", 30.5442, 114.3063, 94, 70),
        _landmark("东湖生态旅游风景区", 30.5585, 114.4010, 91),
        _museum("江汉关博物馆", 30.5840, 114.2910, 87),
        _district("江汉路步行街", 30.5810, 114.2920, 84),
        _district("昙华林", 30.5530, 114.3170, 80),
    ),
}
