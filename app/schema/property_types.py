"""Property type enumeration matching customer specification."""

from enum import Enum


class PropertyType(str, Enum):
    LAND = "买卖-土地"
    MANSION = "买卖-公寓塔楼"
    HOUSE = "买卖-一户建"
    INVESTMENT = "买卖-投资物件"
    RENTAL = "租房"
    OTHER = "其他物件"
