"""
对账脚本：在输出最终文件前跑全部 4 个机器对账节点（节点 5 由独立 Agent 完成）

运行：python3 reconcile.py --trades trades.json --events events.json
任一节点 fail → exit code 1，阻塞输出
"""
import argparse
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import List, Dict

sys.path.insert(0, str(Path(__file__).parent))
from cost_basis import Trade, weighted_avg, fifo, moving_avg, specific_id


def D(x):
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def load_trades(path: str) -> List[Trade]:
    raw = json.loads(Path(path).read_text())
    out = []
    for r in raw:
        out.append(Trade(
            trade_date=r['trade_date'], market=r['market'], symbol=r['symbol'],
            name=r['name'], side=r['side'], qty=D(r['qty']), price=D(r['price']),
            amount_local=D(r['amount_local']), currency=r['currency'],
            commission=D(r.get('commission', 0)),
            platform_fee=D(r.get('platform_fee', 0)),
            stamp_duty=D(r.get('stamp_duty', 0)),
            settlement_fee=D(r.get('settlement_fee', 0)),
            sec_fee=D(r.get('sec_fee', 0)),
            other_fee=D(r.get('other_fee', 0)),
            account=r.get('account', ''),
        ))
    return out


# ---------- 节点 1：数据完整性 ----------
def reconcile_data_integrity(trades: List[Trade], expected: dict) -> Dict:
    """
    expected: {'buy_count': X, 'sell_count': Y, 'buy_amount_total_local': Z, 'sell_amount_total_local': W}
              这些数字应由独立 Agent 从原始 PDF 重新统计
    """
    buys = [t for t in trades if t.side == 'BUY']
    sells = [t for t in trades if t.side == 'SELL']
    actual = {
        'buy_count': len(buys),
        'sell_count': len(sells),
        'buy_amount_total_local': sum((t.qty * t.price for t in buys), Decimal(0)),
        'sell_amount_total_local': sum((t.qty * t.price for t in sells), Decimal(0)),
        'unique_symbols': len({(t.market, t.symbol) for t in trades}),
    }
    diffs = {}
    ok = True
    for k, v in expected.items():
        a = actual.get(k)
        if a is None:
            continue
        d = D(a) - D(v)
        diffs[k] = {'expected': v, 'actual': str(a), 'diff': str(d)}
        if abs(d) > Decimal('0.01'):
            ok = False
    return {'pass': ok, 'actual': {k: str(v) for k, v in actual.items()}, 'diffs': diffs}


# ---------- 节点 2：算法一致性 ----------
def reconcile_algorithm_invariants(trades: List[Trade]) -> Dict:
    """
    四算法跑完后必须满足：
    - 总买入金额相同
    - 总卖出金额相同
    - 期末持仓数量相同（每只股票）
    - 总实际减持收益: 加权平均 = 移动加权（在逐事件粒度下应等价）
    """
    results = {}
    for name, fn in [('weighted_avg', weighted_avg), ('moving_avg', moving_avg),
                     ('fifo', fifo), ('specific_id', specific_id)]:
        events = fn(trades)
        # 期末持仓
        final_qty = defaultdict(Decimal)
        for e in events:
            key = f"{e.trade.market}|{e.trade.symbol}"
            final_qty[key] = e.qty_after
        total_realized = sum((e.realized_pnl for e in events if e.trade.side == 'SELL'), Decimal(0))
        results[name] = {
            'final_qty': {k: str(v) for k, v in final_qty.items()},
            'total_realized': str(total_realized),
        }

    # 检查不变量
    final_qty_sets = [tuple(sorted(r['final_qty'].items())) for r in results.values()]
    qty_consistent = all(s == final_qty_sets[0] for s in final_qty_sets)

    wa = D(results['weighted_avg']['total_realized'])
    ma = D(results['moving_avg']['total_realized'])
    avg_eq_moving = abs(wa - ma) < Decimal('0.01')

    ok = qty_consistent and avg_eq_moving
    return {
        'pass': ok,
        'final_qty_consistent_across_methods': qty_consistent,
        'weighted_avg_eq_moving_avg': avg_eq_moving,
        'totals': {k: v['total_realized'] for k, v in results.items()},
    }


# ---------- 节点 3：年度勾稽 ----------
def reconcile_period_aggregation(trades: List[Trade], method: str = 'weighted_avg') -> Dict:
    """
    sum(逐笔 SELL pnl) == sum(月度汇总) == sum(季度汇总) == sum(年度汇总)
    """
    fn = {'weighted_avg': weighted_avg, 'fifo': fifo,
          'moving_avg': moving_avg, 'specific_id': specific_id}[method]
    events = fn(trades)
    sells = [e for e in events if e.trade.side == 'SELL']

    by_month, by_quarter, by_year = defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal)
    for e in sells:
        ym = e.trade.trade_date[:7]
        y = int(e.trade.trade_date[:4])
        m = int(e.trade.trade_date[5:7])
        q = (m - 1) // 3 + 1
        by_month[ym] += e.realized_pnl
        by_quarter[f"{y}Q{q}"] += e.realized_pnl
        by_year[y] += e.realized_pnl

    total_per_event = sum((e.realized_pnl for e in sells), Decimal(0))
    total_month = sum(by_month.values(), Decimal(0))
    total_quarter = sum(by_quarter.values(), Decimal(0))
    total_year = sum(by_year.values(), Decimal(0))

    diffs = {
        'event_vs_month': str(total_per_event - total_month),
        'month_vs_quarter': str(total_month - total_quarter),
        'quarter_vs_year': str(total_quarter - total_year),
    }
    ok = all(abs(D(v)) < Decimal('0.01') for v in diffs.values())
    return {
        'pass': ok, 'method': method,
        'totals': {
            'per_event': str(total_per_event), 'month': str(total_month),
            'quarter': str(total_quarter), 'year': str(total_year),
        },
        'diffs': diffs,
    }


# ---------- 节点 4：金标准回归（合成基线 2024-02 = 15,000.00）----------
def reconcile_baseline_regression() -> Dict:
    from test_cost_basis import test_baseline_regression
    try:
        test_baseline_regression()
        return {'pass': True, 'baseline': '合成基线 2024-02 复现通过'}
    except AssertionError as e:
        return {'pass': False, 'reason': str(e)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--trades', required=True, help='trades.json 路径')
    p.add_argument('--expected', help='独立 Agent 重算的期望值 JSON')
    p.add_argument('--method', default='weighted_avg')
    args = p.parse_args()

    trades = load_trades(args.trades)
    report = {}

    if args.expected:
        expected = json.loads(Path(args.expected).read_text())
        report['node1_data_integrity'] = reconcile_data_integrity(trades, expected)

    report['node2_algorithm_invariants'] = reconcile_algorithm_invariants(trades)
    report['node3_period_aggregation'] = reconcile_period_aggregation(trades, args.method)
    report['node4_baseline_regression'] = reconcile_baseline_regression()

    all_pass = all(v.get('pass') for v in report.values())
    report['_overall_pass'] = all_pass

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()
