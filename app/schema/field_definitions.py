"""Complete field definitions for all 6 property types.

Each type defines sections in customer-specified order.  Every field has:
  - label:  Chinese display name (客户字段名)
  - key:    Dot-separated JSON key (e.g. basic.city_ward)
  - dtype:  Suggested data type
  - source: Where the value comes from (scrape/ocr/calculate/ai_generate/ai_query)
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Dict, List, Optional

from app.schema.property_types import PropertyType


@dataclass
class Field:
    label: str
    key: str
    dtype: str = "string"
    source: str = "scrape"


@dataclass
class Section:
    name: str
    fields: List[Field] = dataclass_field(default_factory=list)


# ---------------------------------------------------------------------------
# 买卖-土地
# ---------------------------------------------------------------------------

LAND_SECTIONS: List[Section] = [
    Section("媒体与链接", [
        Field("图片", "media.images", "array<string>", "scrape"),
        Field("视频", "media.videos", "array<string>", "scrape"),
        Field("Vr链接", "media.vr_links", "array<string>", "scrape"),
    ]),
    Section("基本信息", [
        Field("所在城市", "basic.city_ward", "string", "scrape"),
        Field("物件名称", "basic.property_name", "object", "ai_generate"),
        Field("物件类型", "basic.property_type", "enum<string>", "rule"),
        Field("物件标签", "basic.tags", "array<string>", "ai_generate"),
        Field("售价（日元）", "basic.price_jpy", "int", "scrape"),
        Field("售价（人民币）", "basic.price_cny", "int", "calculate"),
        Field("土地面积", "basic.land_area", "float", "scrape"),
        Field("单价", "basic.unit_price", "float", "calculate"),
        Field("预估租金", "basic.estimated_rent", "int", "ai_generate"),
        Field("地址", "basic.address", "string", "scrape"),
        Field("经度", "basic.longitude", "float", "ai_query"),
        Field("纬度", "basic.latitude", "float", "ai_query"),
        Field("附近标志", "basic.nearby_landmark", "string", "ai_query"),
        Field("建成日期", "basic.built_month", "string", "scrape"),
    ]),
    Section("基础详情信息", [
        Field("物件名", "detail.building_name", "string", "scrape"),
        Field("交通", "detail.access", "array<object>", "scrape"),
        Field("现状", "detail.land_or_invest_status", "enum<string>", "scrape"),
        Field("土地面积", "basic.land_area", "float", "scrape"),
        Field("总区划数", "detail.total_lots", "int", "scrape"),
        Field("土地权利", "land.rights", "enum<string>", "scrape"),
        Field("引渡时间文本", "deal.delivery_text", "enum<string>", "scrape"),
        Field("引渡时间", "deal.delivery_time", "string", "scrape"),
        Field("备注", "detail.remark", "string", "ai_generate"),
    ]),
    Section("物件介绍", [
        Field("物件介绍", "ai.description_candidates", "array<string>", "ai_generate"),
    ]),
    Section("投资分析信息", [
        Field("物件价格", "analysis.property_price", "int", "scrape"),
        Field("不动产取得税", "analysis.acquisition_tax", "int", "calculate"),
        Field("登录免许税", "analysis.registration_tax", "int", "calculate"),
        Field("印花税", "analysis.stamp_tax", "int", "calculate"),
        Field("司法书士费", "analysis.scrivener_fee", "int", "calculate"),
        Field("中介费", "analysis.brokerage_fee", "int", "calculate"),
        Field("实际总支出", "analysis.total_spend", "int", "calculate"),
        Field("固定资产税", "analysis.fixed_asset_tax", "int", "calculate"),
        Field("都市计划税", "analysis.city_planning_tax", "int", "calculate"),
        Field("管理费", "analysis_or_detail.management_fee", "int", "scrape"),
        Field("修缮费", "analysis_or_detail.repair_fee", "int", "scrape"),
        Field("托管费", "analysis.trust_fee", "int", "scrape"),
        Field("其他费用", "analysis_or_detail.other_fees", "array<object>", "scrape"),
        Field("一年总成本", "analysis.annual_total_cost", "int", "calculate"),
        Field("月租金收入", "analysis.monthly_rent_income", "int", "ai_generate"),
        Field("年租金收入", "analysis.annual_rent_income", "int", "calculate"),
        Field("年回报率", "analysis.annual_roi_pct", "float", "calculate"),
    ]),
    Section("土地信息", [
        Field("土地权利", "land.rights", "enum<string>", "scrape"),
        Field("土地面积", "basic.land_area", "float", "scrape"),
        Field("建蔽率", "land.building_coverage_pct", "float", "scrape"),
        Field("容积率", "land.far_pct", "float", "scrape"),
        Field("用途地域", "land.zoning", "enum<string>", "scrape"),
        Field("私道负担", "land.private_road_burden", "string", "scrape"),
        Field("限制事项", "land.restrictions", "string", "scrape"),
    ]),
    Section("周边配套", [
        Field("学校", "poi.schools", "array<object>", "ai_query"),
        Field("商圈", "poi.business_districts", "array<object>", "ai_query"),
        Field("公园设施", "poi.parks", "array<object>", "ai_query"),
    ]),
]

# ---------------------------------------------------------------------------
# 买卖-公寓塔楼
# ---------------------------------------------------------------------------

MANSION_SECTIONS: List[Section] = [
    Section("媒体与链接", [
        Field("图片", "media.images", "array<string>", "scrape"),
        Field("视频", "media.videos", "array<string>", "scrape"),
        Field("Vr链接", "media.vr_links", "array<string>", "scrape"),
    ]),
    Section("基本信息", [
        Field("所在城市", "basic.city_ward", "string", "scrape"),
        Field("物件名称", "basic.property_name", "object", "ai_generate"),
        Field("物件类型", "basic.property_type", "enum<string>", "rule"),
        Field("物件标签", "basic.tags", "array<string>", "ai_generate"),
        Field("售价（日元）", "basic.price_jpy", "int", "scrape"),
        Field("售价（人民币）", "basic.price_cny", "int", "calculate"),
        Field("户型", "basic.layout_cn", "string", "scrape"),
        Field("面积", "basic.area", "float", "scrape"),
        Field("单价", "basic.unit_price", "float", "calculate"),
        Field("预估租金", "basic.estimated_rent", "int", "ai_generate"),
        Field("地址", "basic.address", "string", "scrape"),
        Field("经度", "basic.longitude", "float", "ai_query"),
        Field("纬度", "basic.latitude", "float", "ai_query"),
        Field("附近标志", "basic.nearby_landmark", "string", "ai_query"),
        Field("建成日期", "basic.built_month", "string", "scrape"),
    ]),
    Section("基础详情信息", [
        Field("物件名", "detail.building_name", "string", "scrape"),
        Field("交通", "detail.access", "array<object>", "scrape"),
        Field("总户数", "detail.total_units", "int", "scrape"),
        Field("类型", "detail.sub_type", "enum<string>", "scrape"),
        Field("户型", "basic.layout_cn", "string", "scrape"),
        Field("构造", "detail.structure", "enum<string>", "scrape"),
        Field("朝向", "detail.orientation", "enum<string>", "scrape"),
        Field("专有面积", "detail.exclusive_area", "float", "scrape"),
        Field("其他面积", "detail.other_areas", "array<object>", "scrape"),
        Field("所在楼层", "detail.floor", "int", "scrape"),
        Field("总楼层", "detail.total_floors", "int", "scrape"),
        Field("建成日期", "basic.built_month", "string", "scrape"),
    ]),
    Section("物件介绍", [
        Field("物件介绍", "ai.description_candidates", "array<string>", "ai_generate"),
    ]),
    Section("投资分析信息", [
        Field("物件价格", "analysis.property_price", "int", "scrape"),
        Field("不动产取得税", "analysis.acquisition_tax", "int", "calculate"),
        Field("登录免许税", "analysis.registration_tax", "int", "calculate"),
        Field("印花税", "analysis.stamp_tax", "int", "calculate"),
        Field("司法书士费", "analysis.scrivener_fee", "int", "calculate"),
        Field("中介费", "analysis.brokerage_fee", "int", "calculate"),
        Field("实际总支出", "analysis.total_spend", "int", "calculate"),
        Field("固定资产税", "analysis.fixed_asset_tax", "int", "calculate"),
        Field("都市计划税", "analysis.city_planning_tax", "int", "calculate"),
        Field("管理费", "analysis_or_detail.management_fee", "int", "scrape"),
        Field("修缮费", "analysis_or_detail.repair_fee", "int", "scrape"),
        Field("托管费", "analysis.trust_fee", "int", "scrape"),
        Field("其他费用", "analysis_or_detail.other_fees", "array<object>", "scrape"),
        Field("一年总成本", "analysis.annual_total_cost", "int", "calculate"),
        Field("月租金收入", "analysis.monthly_rent_income", "int", "ai_generate"),
        Field("年租金收入", "analysis.annual_rent_income", "int", "calculate"),
        Field("年回报率", "analysis.annual_roi_pct", "float", "calculate"),
    ]),
    Section("物件信息详情", [
        Field("物件现状", "detail.property_status", "enum<string>", "scrape"),
        Field("总户数", "detail.total_units", "int", "scrape"),
        Field("停车场", "detail.parking", "string", "scrape"),
        Field("修缮费", "analysis_or_detail.repair_fee", "int", "scrape"),
        Field("管理费", "analysis_or_detail.management_fee", "int", "scrape"),
        Field("管理方式", "management.method", "enum<string>", "scrape"),
        Field("管理公司", "management.company", "string", "scrape"),
        Field("施工公司", "building.constructor", "string", "scrape"),
        Field("其他费用", "analysis_or_detail.other_fees", "array<object>", "scrape"),
        Field("土地权利", "land.rights", "enum<string>", "scrape"),
        Field("用途地域", "land.zoning", "enum<string>", "scrape"),
        Field("引渡时间文本", "deal.delivery_text", "enum<string>", "scrape"),
        Field("引渡时间", "deal.delivery_time", "string", "scrape"),
        Field("备注", "detail.remark", "string", "ai_generate"),
    ]),
    Section("物件配套", [
        Field("配套设施", "amenities.facilities", "array<string>", "scrape"),
    ]),
    Section("周边配套", [
        Field("学校", "poi.schools", "array<object>", "ai_query"),
        Field("商圈", "poi.business_districts", "array<object>", "ai_query"),
        Field("公园设施", "poi.parks", "array<object>", "ai_query"),
    ]),
]

# ---------------------------------------------------------------------------
# 买卖-一户建
# ---------------------------------------------------------------------------

HOUSE_SECTIONS: List[Section] = [
    Section("媒体与链接", [
        Field("图片", "media.images", "array<string>", "scrape"),
        Field("视频", "media.videos", "array<string>", "scrape"),
        Field("Vr链接", "media.vr_links", "array<string>", "scrape"),
    ]),
    Section("基本信息", [
        Field("所在城市", "basic.city_ward", "string", "scrape"),
        Field("物件名称", "basic.property_name", "object", "ai_generate"),
        Field("物件类型", "basic.property_type", "enum<string>", "rule"),
        Field("物件标签", "basic.tags", "array<string>", "ai_generate"),
        Field("售价（日元）", "basic.price_jpy", "int", "scrape"),
        Field("售价（人民币）", "basic.price_cny", "int", "calculate"),
        Field("户型", "basic.layout_cn", "string", "scrape"),
        Field("建筑面积", "basic.building_area", "float", "scrape"),
        Field("单价", "basic.unit_price", "float", "calculate"),
        Field("预估租金", "basic.estimated_rent", "int", "ai_generate"),
        Field("地址", "basic.address", "string", "scrape"),
        Field("经度", "basic.longitude", "float", "ai_query"),
        Field("纬度", "basic.latitude", "float", "ai_query"),
        Field("附近标志", "basic.nearby_landmark", "string", "ai_query"),
        Field("建成日期", "basic.built_month", "string", "scrape"),
    ]),
    Section("基础详情信息", [
        Field("物件名", "detail.building_name", "string", "scrape"),
        Field("交通", "detail.access", "array<object>", "scrape"),
        Field("户型", "basic.layout_cn", "string", "scrape"),
        Field("构造", "detail.structure", "enum<string>", "scrape"),
        Field("朝向", "detail.orientation", "enum<string>", "scrape"),
        Field("土地面积", "basic.land_area", "float", "scrape"),
        Field("建筑面积", "basic.building_area", "float", "scrape"),
        Field("其他面积", "detail.other_areas", "array<object>", "scrape"),
        Field("总楼层", "detail.total_floors", "int", "scrape"),
        Field("建成日期", "basic.built_month", "string", "scrape"),
    ]),
    Section("物件介绍", [
        Field("物件介绍", "ai.description_candidates", "array<string>", "ai_generate"),
    ]),
    Section("投资分析信息", [
        Field("物件价格", "analysis.property_price", "int", "scrape"),
        Field("不动产取得税", "analysis.acquisition_tax", "int", "calculate"),
        Field("登录免许税", "analysis.registration_tax", "int", "calculate"),
        Field("印花税", "analysis.stamp_tax", "int", "calculate"),
        Field("司法书士费", "analysis.scrivener_fee", "int", "calculate"),
        Field("中介费", "analysis.brokerage_fee", "int", "calculate"),
        Field("实际总支出", "analysis.total_spend", "int", "calculate"),
        Field("固定资产税", "analysis.fixed_asset_tax", "int", "calculate"),
        Field("都市计划税", "analysis.city_planning_tax", "int", "calculate"),
        Field("管理费", "analysis_or_detail.management_fee", "int", "scrape"),
        Field("修缮费", "analysis_or_detail.repair_fee", "int", "scrape"),
        Field("托管费", "analysis.trust_fee", "int", "scrape"),
        Field("其他费用", "analysis_or_detail.other_fees", "array<object>", "scrape"),
        Field("一年总成本", "analysis.annual_total_cost", "int", "calculate"),
        Field("月租金收入", "analysis.monthly_rent_income", "int", "ai_generate"),
        Field("年租金收入", "analysis.annual_rent_income", "int", "calculate"),
        Field("年回报率", "analysis.annual_roi_pct", "float", "calculate"),
    ]),
    Section("物件信息", [
        Field("总户数", "detail.total_units", "int", "scrape"),
        Field("物件现状", "detail.property_status", "enum<string>", "scrape"),
        Field("停车场", "detail.parking", "string", "scrape"),
        Field("修缮费", "analysis_or_detail.repair_fee", "int", "scrape"),
        Field("管理费", "analysis_or_detail.management_fee", "int", "scrape"),
        Field("其他费用", "analysis_or_detail.other_fees", "array<object>", "scrape"),
        Field("引渡时间文本", "deal.delivery_text", "enum<string>", "scrape"),
        Field("引渡时间", "deal.delivery_time", "string", "scrape"),
    ]),
    Section("建筑信息", [
        Field("物件名", "detail.building_name", "string", "scrape"),
        Field("建筑面积", "basic.building_area", "float", "scrape"),
        Field("建成时间", "building.built_month", "string", "scrape"),
        Field("构造", "detail.structure", "enum<string>", "scrape"),
        Field("施工公司", "building.constructor", "string", "scrape"),
        Field("所在地", "building.location", "string", "scrape"),
        Field("翻新", "building.renovation", "string", "scrape"),
    ]),
    Section("土地信息", [
        Field("土地权利", "land.rights", "enum<string>", "scrape"),
        Field("土地面积", "basic.land_area", "float", "scrape"),
        Field("建蔽率", "land.building_coverage_pct", "float", "scrape"),
        Field("容积率", "land.far_pct", "float", "scrape"),
        Field("用途地域", "land.zoning", "enum<string>", "scrape"),
        Field("私道负担", "land.private_road_burden", "string", "scrape"),
        Field("限制事项", "land.restrictions", "string", "scrape"),
    ]),
    Section("物件配套", [
        Field("配套设施", "amenities.facilities", "array<string>", "scrape"),
    ]),
    Section("周边配套", [
        Field("学校", "poi.schools", "array<object>", "ai_query"),
        Field("商圈", "poi.business_districts", "array<object>", "ai_query"),
        Field("公园设施", "poi.parks", "array<object>", "ai_query"),
    ]),
]

# ---------------------------------------------------------------------------
# 买卖-投资物件
# ---------------------------------------------------------------------------

INVESTMENT_SECTIONS: List[Section] = [
    Section("媒体与链接", [
        Field("图片", "media.images", "array<string>", "scrape"),
        Field("视频", "media.videos", "array<string>", "scrape"),
        Field("Vr链接", "media.vr_links", "array<string>", "scrape"),
    ]),
    Section("基本信息", [
        Field("所在城市", "basic.city_ward", "string", "scrape"),
        Field("物件名称", "basic.property_name", "object", "ai_generate"),
        Field("物件类型", "basic.property_type", "enum<string>", "rule"),
        Field("物件标签", "basic.tags", "array<string>", "ai_generate"),
        Field("售价（日元）", "basic.price_jpy", "int", "scrape"),
        Field("售价（人民币）", "basic.price_cny", "int", "calculate"),
        Field("建筑面积", "basic.building_area", "float", "scrape"),
        Field("单价", "basic.unit_price", "float", "calculate"),
        Field("回报率", "basic.estimated_roi_pct", "float", "ai_generate"),
        Field("预估租金", "basic.estimated_rent", "int", "ai_generate"),
        Field("地址", "basic.address", "string", "scrape"),
        Field("经度", "basic.longitude", "float", "ai_query"),
        Field("纬度", "basic.latitude", "float", "ai_query"),
        Field("附近标志", "basic.nearby_landmark", "string", "ai_query"),
        Field("建成日期", "basic.built_month", "string", "scrape"),
    ]),
    Section("基础详情信息", [
        Field("物件名", "detail.building_name", "string", "scrape"),
        Field("交通", "detail.access", "array<object>", "scrape"),
        Field("现状", "detail.land_or_invest_status", "enum<string>", "scrape"),
        Field("交易形式", "detail.transaction_form", "enum<string>", "scrape"),
        Field("土地面积", "basic.land_area", "float", "scrape"),
        Field("建筑面积", "basic.building_area", "float", "scrape"),
        Field("其他面积", "detail.other_areas", "array<object>", "scrape"),
        Field("修缮费", "analysis_or_detail.repair_fee", "int", "scrape"),
        Field("管理费", "analysis_or_detail.management_fee", "int", "scrape"),
        Field("其他费用", "analysis_or_detail.other_fees", "array<object>", "scrape"),
        Field("土地权利", "land.rights", "enum<string>", "scrape"),
        Field("引渡时间文本", "deal.delivery_text", "enum<string>", "scrape"),
        Field("引渡时间", "deal.delivery_time", "string", "scrape"),
        Field("备注", "detail.remark", "string", "ai_generate"),
    ]),
    Section("物件介绍", [
        Field("物件介绍", "ai.description_candidates", "array<string>", "ai_generate"),
    ]),
    Section("投资分析信息", [
        Field("物件价格", "analysis.property_price", "int", "scrape"),
        Field("不动产取得税", "analysis.acquisition_tax", "int", "calculate"),
        Field("登录免许税", "analysis.registration_tax", "int", "calculate"),
        Field("印花税", "analysis.stamp_tax", "int", "calculate"),
        Field("司法书士费", "analysis.scrivener_fee", "int", "calculate"),
        Field("中介费", "analysis.brokerage_fee", "int", "calculate"),
        Field("实际总支出", "analysis.total_spend", "int", "calculate"),
        Field("固定资产税", "analysis.fixed_asset_tax", "int", "calculate"),
        Field("都市计划税", "analysis.city_planning_tax", "int", "calculate"),
        Field("管理费", "analysis_or_detail.management_fee", "int", "scrape"),
        Field("修缮费", "analysis_or_detail.repair_fee", "int", "scrape"),
        Field("托管费", "analysis.trust_fee", "int", "scrape"),
        Field("其他费用", "analysis_or_detail.other_fees", "array<object>", "scrape"),
        Field("一年总成本", "analysis.annual_total_cost", "int", "calculate"),
        Field("月租金收入", "analysis.monthly_rent_income", "int", "ai_generate"),
        Field("年租金收入", "analysis.annual_rent_income", "int", "calculate"),
        Field("年回报率", "analysis.annual_roi_pct", "float", "calculate"),
    ]),
    Section("建筑信息", [
        Field("建成日期", "basic.built_month", "string", "scrape"),
        Field("构造", "detail.structure", "enum<string>", "scrape"),
        Field("总面积", "building.total_area", "float", "scrape"),
        Field("总楼层", "detail.total_floors", "int", "scrape"),
        Field("总套数", "building.total_units", "int", "scrape"),
        Field("抗震构造", "building.seismic_type", "enum<string>", "scrape"),
        Field("所在地", "building.location", "string", "scrape"),
        Field("施工公司", "building.constructor", "string", "scrape"),
        Field("翻新", "building.renovation", "string", "scrape"),
    ]),
    Section("土地信息", [
        Field("土地权利", "land.rights", "enum<string>", "scrape"),
        Field("土地面积", "basic.land_area", "float", "scrape"),
        Field("建蔽率", "land.building_coverage_pct", "float", "scrape"),
        Field("容积率", "land.far_pct", "float", "scrape"),
        Field("用途地域", "land.zoning", "enum<string>", "scrape"),
        Field("私道负担", "land.private_road_burden", "string", "scrape"),
        Field("限制事项", "land.restrictions", "string", "scrape"),
    ]),
    Section("物件配套", [
        Field("配套设施", "amenities.facilities", "array<string>", "scrape"),
    ]),
    Section("周边配套", [
        Field("学校", "poi.schools", "array<object>", "ai_query"),
        Field("商圈", "poi.business_districts", "array<object>", "ai_query"),
        Field("公园设施", "poi.parks", "array<object>", "ai_query"),
    ]),
]

# ---------------------------------------------------------------------------
# 租房
# ---------------------------------------------------------------------------

RENTAL_SECTIONS: List[Section] = [
    Section("媒体与链接", [
        Field("图片", "media.images", "array<string>", "scrape"),
        Field("视频", "media.videos", "array<string>", "scrape"),
        Field("Vr链接", "media.vr_links", "array<string>", "scrape"),
    ]),
    Section("基本信息", [
        Field("所在城市", "basic.city_ward", "string", "scrape"),
        Field("物件名称", "basic.property_name", "object", "ai_generate"),
        Field("物件类型", "basic.property_type", "enum<string>", "rule"),
        Field("物件标签", "basic.tags", "array<string>", "ai_generate"),
        Field("售价（日元）", "basic.price_jpy", "int", "scrape"),
        Field("售价（人民币）", "basic.price_cny", "int", "calculate"),
        Field("户型", "basic.layout_cn", "string", "scrape"),
        Field("地址", "basic.address", "string", "scrape"),
        Field("经度", "basic.longitude", "float", "ai_query"),
        Field("纬度", "basic.latitude", "float", "ai_query"),
        Field("附近标志", "basic.nearby_landmark", "string", "ai_query"),
        Field("建成日期", "basic.built_month", "string", "scrape"),
    ]),
    Section("基础详情信息", [
        Field("物件名", "detail.building_name", "string", "scrape"),
        Field("交通", "detail.access", "array<object>", "scrape"),
        Field("类型", "detail.sub_type", "enum<string>", "scrape"),
        Field("户型", "basic.layout_cn", "string", "scrape"),
        Field("朝向", "detail.orientation", "enum<string>", "scrape"),
        Field("楼层", "detail.floor", "int", "scrape"),
        Field("总楼层", "detail.total_floors", "int", "scrape"),
        Field("面积", "basic.area", "float", "scrape"),
    ]),
    Section("物件介绍", [
        Field("物件介绍", "ai.description_candidates", "array<string>", "ai_generate"),
    ]),
    Section("物件信息", [
        Field("房租", "rent.rent", "int", "scrape"),
        Field("户型", "basic.layout_cn", "string", "scrape"),
        Field("总户数", "detail.total_units", "int", "scrape"),
        Field("共益费", "rent.common_service_fee", "int", "scrape"),
        Field("管理费", "analysis_or_detail.management_fee", "int", "scrape"),
        Field("面积", "basic.area", "float", "scrape"),
        Field("停车场", "detail.parking", "string", "scrape"),
        Field("合同期", "rent.contract_term", "string", "scrape"),
        Field("构造", "detail.structure", "enum<string>", "scrape"),
        Field("建成日期", "basic.built_month", "string", "scrape"),
        Field("所在楼层", "detail.floor", "int", "scrape"),
        Field("总层数", "detail.total_floors", "int", "scrape"),
    ]),
    Section("初期费用评估", [
        Field("租金", "rent.initial_rent", "int", "scrape"),
        Field("押金", "rent.deposit", "int", "scrape"),
        Field("礼金", "rent.key_money", "int", "scrape"),
        Field("中介费", "analysis.brokerage_fee", "int", "calculate"),
        Field("其他费用", "analysis_or_detail.other_fees", "array<object>", "scrape"),
        Field("总费用", "rent.initial_total_fee", "int", "calculate"),
        Field("备注", "detail.remark", "string", "ai_generate"),
    ]),
    Section("物件配套", [
        Field("配套设施", "amenities.facilities", "array<string>", "scrape"),
    ]),
    Section("周边配套", [
        Field("学校", "poi.schools", "array<object>", "ai_query"),
        Field("商圈", "poi.business_districts", "array<object>", "ai_query"),
        Field("公园设施", "poi.parks", "array<object>", "ai_query"),
    ]),
]

# ---------------------------------------------------------------------------
# 其他物件
# ---------------------------------------------------------------------------

OTHER_SECTIONS: List[Section] = [
    Section("媒体与链接", [
        Field("图片", "media.images", "array<string>", "scrape"),
        Field("视频", "media.videos", "array<string>", "scrape"),
        Field("Vr链接", "media.vr_links", "array<string>", "scrape"),
    ]),
    Section("基本信息", [
        Field("所在城市", "basic.city_ward", "string", "scrape"),
        Field("物件名称", "basic.property_name", "object", "ai_generate"),
        Field("物件类型", "basic.property_type", "enum<string>", "rule"),
        Field("物件标签", "basic.tags", "array<string>", "ai_generate"),
        Field("售价（日元）", "basic.price_jpy", "int", "scrape"),
        Field("售价（人民币）", "basic.price_cny", "int", "calculate"),
        Field("月租金", "basic.monthly_rent", "int", "ai_generate"),
        Field("回报率", "basic.estimated_roi_pct", "float", "ai_generate"),
        Field("地址", "basic.address", "string", "scrape"),
        Field("经度", "basic.longitude", "float", "ai_query"),
        Field("纬度", "basic.latitude", "float", "ai_query"),
        Field("附近标志", "basic.nearby_landmark", "string", "ai_query"),
        Field("建成日期", "basic.built_month", "string", "scrape"),
    ]),
    Section("物件介绍", [
        Field("物件介绍", "ai.description_candidates", "array<string>", "ai_generate"),
    ]),
    Section("投资分析信息", [
        Field("物件价格", "analysis.property_price", "int", "scrape"),
        Field("不动产取得税", "analysis.acquisition_tax", "int", "calculate"),
        Field("登录免许税", "analysis.registration_tax", "int", "calculate"),
        Field("印花税", "analysis.stamp_tax", "int", "calculate"),
        Field("司法书士费", "analysis.scrivener_fee", "int", "calculate"),
        Field("中介费", "analysis.brokerage_fee", "int", "calculate"),
        Field("实际总支出", "analysis.total_spend", "int", "calculate"),
        Field("固定资产税", "analysis.fixed_asset_tax", "int", "calculate"),
        Field("都市计划税", "analysis.city_planning_tax", "int", "calculate"),
        Field("管理费", "analysis_or_detail.management_fee", "int", "scrape"),
        Field("修缮费", "analysis_or_detail.repair_fee", "int", "scrape"),
        Field("托管费", "analysis.trust_fee", "int", "scrape"),
        Field("其他费用", "analysis_or_detail.other_fees", "array<object>", "scrape"),
        Field("一年总成本", "analysis.annual_total_cost", "int", "calculate"),
        Field("月租金收入", "analysis.monthly_rent_income", "int", "ai_generate"),
        Field("年租金收入", "analysis.annual_rent_income", "int", "calculate"),
        Field("年回报率", "analysis.annual_roi_pct", "float", "calculate"),
    ]),
    Section("周边配套", [
        Field("学校", "poi.schools", "array<object>", "ai_query"),
        Field("商圈", "poi.business_districts", "array<object>", "ai_query"),
        Field("公园设施", "poi.parks", "array<object>", "ai_query"),
    ]),
]


# ---------------------------------------------------------------------------
# Registry & helpers
# ---------------------------------------------------------------------------

_SECTIONS_MAP: Dict[PropertyType, List[Section]] = {
    PropertyType.LAND: LAND_SECTIONS,
    PropertyType.MANSION: MANSION_SECTIONS,
    PropertyType.HOUSE: HOUSE_SECTIONS,
    PropertyType.INVESTMENT: INVESTMENT_SECTIONS,
    PropertyType.RENTAL: RENTAL_SECTIONS,
    PropertyType.OTHER: OTHER_SECTIONS,
}


def get_field_definitions(ptype: PropertyType) -> List[Section]:
    """Return the ordered list of sections for a property type."""
    return _SECTIONS_MAP.get(ptype, OTHER_SECTIONS)


def get_flat_field_names(ptype: PropertyType) -> List[str]:
    """Return a flat, ordered list of field labels (may contain duplicates
    across sections — use key for uniqueness)."""
    result: List[str] = []
    for section in get_field_definitions(ptype):
        for f in section.fields:
            result.append(f.label)
    return result


def get_all_keys(ptype: PropertyType) -> List[str]:
    """Return a flat, ordered list of unique field keys."""
    seen = set()
    result: List[str] = []
    for section in get_field_definitions(ptype):
        for f in section.fields:
            if f.key not in seen:
                seen.add(f.key)
                result.append(f.key)
    return result


def get_key_to_label(ptype: PropertyType) -> Dict[str, str]:
    """Return a mapping from key -> label for a property type."""
    mapping: Dict[str, str] = {}
    for section in get_field_definitions(ptype):
        for f in section.fields:
            if f.key not in mapping:
                mapping[f.key] = f.label
    return mapping
