"""
人民币汇率中间价（中国外汇交易中心）

数据源：assets/cny_mid_rate.json（年末 12-31 中间价缓存）
缺失时调用 --refresh 重新拉取（手动维护）

提供两种轨道：
- 法规口径：年末 12-31 中间价
- 实操口径：买入日所在月最后一日中间价
"""
import argparse
import json
import sys
from decimal import Decimal
from datetime import date, datetime
from pathlib import Path

ASSETS = Path(__file__).parent.parent / 'assets' / 'cny_mid_rate.json'


def load_rates() -> dict:
    if not ASSETS.exists():
        return {}
    return json.loads(ASSETS.read_text())


def year_end_rate(year: int, currency: str) -> Decimal:
    """法规口径：年末 12-31 中间价。currency: USD/HKD/SGD"""
    rates = load_rates()
    key = f"{year}-12-31"
    if key not in rates or currency not in rates[key]:
        raise ValueError(
            f"缺少 {year}-12-31 {currency} 中间价。"
            f"请运行 fx_rate.py --refresh 或手动编辑 {ASSETS}"
        )
    return Decimal(str(rates[key][currency]))


def month_end_rate(year: int, month: int, currency: str) -> Decimal:
    """实操口径：所在月最后一日中间价"""
    rates = load_rates()
    # 找该月最后一个工作日的中间价
    candidates = sorted(k for k in rates if k.startswith(f"{year}-{month:02d}"))
    if not candidates:
        raise ValueError(f"缺少 {year}-{month:02d} {currency} 中间价")
    last_key = candidates[-1]
    if currency not in rates[last_key]:
        raise ValueError(f"{last_key} 缺少 {currency}")
    return Decimal(str(rates[last_key][currency]))


def convert(amount: Decimal, currency: str, ref_date: str, track: str = 'A',
            settlement_year: int = None) -> Decimal:
    """
    amount: 原币金额
    currency: USD / HKD / SGD / CNY
    ref_date: 该笔交易日 YYYY-MM-DD
    track: 'A' = 法规口径（settlement_year 12-31）；'B' = 实操口径（ref_date 上月末）
    """
    if currency in ('CNY', 'RMB'):
        return amount

    if track == 'A':
        if settlement_year is None:
            raise ValueError("track A 需要 settlement_year")
        rate = year_end_rate(settlement_year, currency)
    else:  # B
        d = datetime.strptime(ref_date, '%Y-%m-%d').date()
        # 上一月最后一日
        if d.month == 1:
            y, m = d.year - 1, 12
        else:
            y, m = d.year, d.month - 1
        rate = month_end_rate(y, m, currency)

    return amount * rate


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--refresh', action='store_true', help='提示如何手动更新')
    p.add_argument('--year', type=int)
    p.add_argument('--currency', default='USD')
    args = p.parse_args()

    if args.refresh:
        print(
            "中国外汇交易中心未提供公开 API。请手动从以下渠道获取年末中间价：\n"
            "  https://www.chinamoney.org.cn/chinese/ccprnoticemain/\n"
            "  http://m.safe.gov.cn/safe/rmbhlzjj/index.html\n"
            f"然后编辑 {ASSETS}：\n"
            '{"2024-12-31": {"USD": 7.1884, "HKD": 0.92558, "SGD": 5.2769}}'
        )
        return

    if args.year:
        print(f"{args.year}-12-31 {args.currency}: {year_end_rate(args.year, args.currency)}")


if __name__ == '__main__':
    main()
