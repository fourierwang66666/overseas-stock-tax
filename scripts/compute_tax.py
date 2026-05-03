"""
应纳税额计算（财产转让所得 + 利息股息红利所得）

按 (年度, 国别) 分池：
- 池内逐笔加总 → 正数 × 20% = 应纳税额；负数 → 0（亏损不结转）
- 同年度不同国别独立计算
- 股息独立池子（按国别），可凭境外预扣抵免
"""
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import List, Dict

sys.path.insert(0, str(Path(__file__).parent))
from cost_basis import RealizedEvent
from schema import Dividend
from fx_rate import convert


TAX_RATE = Decimal('0.20')  # 财产转让所得 / 利息股息红利所得 20%


def market_to_country(market: str) -> str:
    return {'HK': 'CN-HK', 'US': 'US', 'SG': 'SG'}.get(market, market)


def compute_capital_gains(events: List[RealizedEvent], settlement_year: int,
                          fx_track: str = 'A') -> Dict:
    """
    财产转让所得计算

    返回：
    {
      'by_pool': { (year, country): {'gross_pnl_local_sum_cny': X, 'tax': Y, 'count': N} },
      'total_tax': Decimal,
      'total_gross_pnl': Decimal,
      'currency_breakdown': {...}
    }
    """
    pools = defaultdict(lambda: {
        'realized_sum_cny': Decimal(0),
        'sells': [],
        'currency_split': defaultdict(Decimal),
    })

    for e in events:
        if e.trade.side != 'SELL':
            continue
        year = int(e.trade.trade_date[:4])
        country = market_to_country(e.trade.market)
        # 卖出收益从原币换算到 CNY
        pnl_cny = convert(
            e.realized_pnl, e.trade.currency, e.trade.trade_date,
            track=fx_track, settlement_year=settlement_year,
        )
        key = (year, country)
        pools[key]['realized_sum_cny'] += pnl_cny
        pools[key]['sells'].append({
            'date': e.trade.trade_date, 'symbol': e.trade.symbol,
            'pnl_local': e.realized_pnl, 'currency': e.trade.currency,
            'pnl_cny': pnl_cny,
        })
        pools[key]['currency_split'][e.trade.currency] += pnl_cny

    by_pool = {}
    total_tax = Decimal(0)
    total_gross_pnl = Decimal(0)
    for (year, country), data in pools.items():
        net = data['realized_sum_cny']
        tax = max(net, Decimal(0)) * TAX_RATE
        by_pool[f"{year}|{country}"] = {
            'year': year, 'country': country,
            'realized_sum_cny': net,
            'tax': tax,
            'sell_count': len(data['sells']),
            'currency_split': dict(data['currency_split']),
        }
        total_tax += tax
        total_gross_pnl += net

    return {
        'by_pool': by_pool,
        'total_tax': total_tax,
        'total_gross_pnl': total_gross_pnl,
        'fx_track': fx_track,
        'settlement_year': settlement_year,
    }


def compute_dividends(divs: List[Dividend], settlement_year: int,
                      fx_track: str = 'A') -> Dict:
    """
    股息红利所得 + 抵免

    返回：
    {
      'by_country': { country: {'gross_cny': X, 'withheld_cny': Y, 'tax_due': Z, 'creditable': W, 'final_due': V} }
    }
    """
    by_country = defaultdict(lambda: {
        'gross_cny': Decimal(0),
        'withheld_cny': Decimal(0),
    })

    for d in divs:
        country = market_to_country(d.market)
        gross_cny = convert(d.gross_amount, d.currency, d.pay_date,
                            track=fx_track, settlement_year=settlement_year)
        withheld_cny = convert(d.withheld_tax, d.currency, d.pay_date,
                               track=fx_track, settlement_year=settlement_year)
        by_country[country]['gross_cny'] += gross_cny
        by_country[country]['withheld_cny'] += withheld_cny

    out = {}
    total_final_due = Decimal(0)
    for country, data in by_country.items():
        tax_due = data['gross_cny'] * TAX_RATE
        creditable = min(data['withheld_cny'], tax_due)  # 抵免限额
        final_due = tax_due - creditable
        out[country] = {
            'gross_cny': data['gross_cny'],
            'withheld_cny': data['withheld_cny'],
            'tax_due_at_20pct': tax_due,
            'creditable': creditable,
            'final_due': final_due,
        }
        total_final_due += final_due

    return {'by_country': out, 'total_final_due': total_final_due}
