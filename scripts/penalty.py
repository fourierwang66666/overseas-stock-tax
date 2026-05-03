"""
滞纳金计算（《税收征管法》第 32 条 日万分之五）

每个所得年度独立起算 = 次年 7-1
终止日 = 用户指定（默认今天）
不可合并多年
"""
from datetime import date
from decimal import Decimal
from typing import Dict


DAILY_RATE = Decimal('0.0005')  # 万分之五


def compute_penalty(annual_tax_due: Dict[int, Decimal],
                    settle_date: date = None) -> Dict:
    """
    annual_tax_due: {2022: Decimal('100000'), 2023: ...}
    settle_date: 实际补缴日；默认今天
    """
    if settle_date is None:
        settle_date = date.today()

    out = {}
    total = Decimal(0)
    for year, tax in annual_tax_due.items():
        if tax <= 0:
            continue
        start = date(year + 1, 7, 1)
        if settle_date <= start:
            penalty = Decimal(0)
            days = 0
        else:
            days = (settle_date - start).days
            penalty = tax * DAILY_RATE * days
        out[year] = {
            'tax_due': tax,
            'start_date': start.isoformat(),
            'settle_date': settle_date.isoformat(),
            'days': days,
            'penalty': penalty,
            'penalty_pct_of_tax': (penalty / tax * 100) if tax > 0 else Decimal(0),
        }
        total += penalty

    return {'by_year': out, 'total_penalty': total}
