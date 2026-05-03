"""统一 schema 定义。所有 parser 输出必须符合本结构。"""
from cost_basis import Trade  # noqa: F401
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class Dividend:
    """股息红利记录（与 Trade 分流，独立按 20% 申报）"""
    ex_date: str          # 除息日
    pay_date: str         # 派息日
    market: str
    symbol: str
    name: str
    gross_amount: Decimal  # 总分红（原币）
    withheld_tax: Decimal  # 境外预扣税（原币）
    currency: str
    account: str = ''


@dataclass
class Transfer:
    """
    持仓平移记录（账户升级 / 跨账户转入转出）
    特征：同券商、同股票、同日期、In/Out 数量相等
    处理：成本基础结转，不产生应税事件
    """
    transfer_date: str    # YYYYMMDD or YYYY-MM-DD
    market: str
    symbol: str
    name: str
    qty: Decimal          # 正数 = 转入；负数 = 转出
    direction: str        # 'In' or 'Out'
    from_account: str = ''
    to_account: str = ''
    note: str = ''        # e.g., 'Account Upgrade'


@dataclass
class CrossBrokerTransfer:
    """
    跨券商持仓转移（富途 ↔ 长桥 / IBKR 等）
    必须由用户手工录入：原券商成本结转单
    skill 无法从单个券商流水自动识别（券商只看到"转入"或"转出"，不知道对方的成本）
    """
    transfer_date: str
    market: str
    symbol: str
    name: str
    qty: Decimal              # 转入数量（正数）
    cost_basis_local: Decimal # 原券商提供的"持仓总成本"（含历史费用，原币）
    currency: str
    from_broker: str          # 'futu' / 'longbridge' / 'ibkr' / ...
    to_account: str = ''
    note: str = ''


@dataclass
class CorporateAction:
    """
    公司行为：拆股 / 合股 / 送股 / 配股 / ADR 调整

    action_type:
      - 'split'           拆股 1:N（如英伟达 10 拆 1：ratio=10，qty×10，avg_cost÷10）
      - 'reverse_split'   合股 N:1（ratio=N，qty÷N，avg_cost×N）
      - 'bonus'           送股/红股（按 bonus_ratio 比例送，qty 增加但 total_cost 不变 → avg_cost 摊薄）
      - 'rights'          配股/供股（按 rights_ratio 配股 + 按 subscription_price 付款 → 等同 BUY，但来源标记）
      - 'adr_ratio'       ADR 比例调整（同 split / reverse_split）
      - 'scrip_dividend'  以股代息（拆为 Dividend + bonus 两个事件，本类只记 bonus 部分）

    单位字段说明：
      - split / reverse_split / adr_ratio：用 ratio_num : ratio_den（如 10:1 → ratio_num=10, ratio_den=1）
      - bonus：bonus_ratio = 每股送 X 股（如 10 送 3 → bonus_ratio=Decimal('0.3')）
      - rights：rights_ratio = 每股可配 X 股；subscription_price = 配股价（原币）
    """
    action_date: str          # YYYY-MM-DD（除权日）
    market: str
    symbol: str
    name: str
    action_type: str          # split / reverse_split / bonus / rights / adr_ratio
    ratio_num: Decimal = Decimal(1)
    ratio_den: Decimal = Decimal(1)
    bonus_ratio: Decimal = Decimal(0)
    rights_ratio: Decimal = Decimal(0)
    subscription_price: Decimal = Decimal(0)
    currency: str = ''
    note: str = ''
