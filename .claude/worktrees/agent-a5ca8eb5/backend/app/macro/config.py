"""宏观指标注册表 - 配置驱动的 akshare 数据采集"""

from dataclasses import dataclass, field


@dataclass
class IndicatorDef:
    """宏观指标定义"""
    name: str             # 中文名
    code: str             # 唯一标识
    category: str         # 分类: monetary/price/growth/trade/sentiment
    akshare_func: str     # akshare 函数名
    akshare_params: dict = field(default_factory=dict)
    value_column: str = ""    # 取值列名
    date_column: str = ""     # 日期列名
    frequency: str = "monthly"  # monthly/quarterly/daily
    unit: str = "%"
    description: str = ""


# 指标注册表
INDICATOR_REGISTRY: list[IndicatorDef] = [
    # === 货币政策 ===
    IndicatorDef(
        name="社会融资规模增量",
        code="social_financing",
        category="monetary",
        akshare_func="macro_china_shrzgm",
        value_column="社会融资规模增量",
        date_column="月份",
        description="反映实体经济从金融体系获得的资金总量",
    ),
    IndicatorDef(
        name="M2货币供应",
        code="m2_supply",
        category="monetary",
        akshare_func="macro_china_money_supply",
        value_column="M2-数量(亿元)",
        date_column="月份",
        description="广义货币供应量，反映流动性",
    ),
    IndicatorDef(
        name="LPR利率",
        code="lpr",
        category="monetary",
        akshare_func="macro_china_lpr",
        value_column="LPR1Y",
        date_column="TRADE_DATE",
        description="贷款市场报价利率，影响融资成本",
    ),
    # === 价格指标 ===
    IndicatorDef(
        name="CPI消费者物价指数",
        code="cpi",
        category="price",
        akshare_func="macro_china_cpi_monthly",
        value_column="同比增长",
        date_column="月份",
        description="消费端通胀水平",
    ),
    IndicatorDef(
        name="PPI生产者物价指数",
        code="ppi",
        category="price",
        akshare_func="macro_china_ppi",
        value_column="当月同比增长",
        date_column="月份",
        description="生产端通胀水平，领先CPI",
    ),
    # === 经济增长 ===
    IndicatorDef(
        name="PMI制造业采购经理指数",
        code="pmi",
        category="growth",
        akshare_func="macro_china_pmi",
        value_column="制造业-指数",
        date_column="月份",
        description="制造业景气度，50为荣枯线",
    ),
    IndicatorDef(
        name="GDP增速",
        code="gdp",
        category="growth",
        akshare_func="macro_china_gdp",
        value_column="同比增长",
        date_column="季度",
        frequency="quarterly",
        description="经济增长速度",
    ),
    # === 贸易 ===
    IndicatorDef(
        name="进出口总额",
        code="trade_balance",
        category="trade",
        akshare_func="macro_china_trade_balance",
        value_column="当月出口额-金额",
        date_column="月份",
        unit="亿美元",
        description="外贸顺差/逆差",
    ),
    # === 市场情绪 ===
    IndicatorDef(
        name="北向资金净流入",
        code="north_flow",
        category="sentiment",
        akshare_func="stock_hsgt_north_net_flow_in_em",
        value_column="当日净流入",
        date_column="日期",
        frequency="daily",
        unit="亿元",
        description="外资流向，反映国际资本态度",
    ),
    IndicatorDef(
        name="融资融券余额",
        code="margin_balance",
        category="sentiment",
        akshare_func="stock_margin_sse",
        value_column="融资余额",
        date_column="信用交易日期",
        frequency="daily",
        unit="亿元",
        description="杠杆资金规模，反映市场情绪",
    ),
]

# 按 code 索引
INDICATOR_MAP: dict[str, IndicatorDef] = {ind.code: ind for ind in INDICATOR_REGISTRY}

# 分类映射
CATEGORIES = {
    "monetary": "货币政策",
    "price": "价格指标",
    "growth": "经济增长",
    "trade": "贸易",
    "sentiment": "市场情绪",
}
