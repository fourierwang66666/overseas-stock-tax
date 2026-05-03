"""
单元测试：用合成数据验证算法正确性

回归基线（合成场景）：2024-02 月度减持收益 = 15,000.00 元
- STOCK_A 2-09 买 1,000 @ 100.00
- STOCK_B 2-09 买   500 @ 200.00
- STOCK_C 2-18 买 2,000 @  50.00
- STOCK_B 2-18 卖   500 @ 220.00 → +10,000.00
- STOCK_C 2-22 卖 1,000 @  55.00 → + 5,000.00

跑：python3 test_cost_basis.py
"""
from decimal import Decimal
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from cost_basis import Trade, weighted_avg, fifo


def D(x):
    return Decimal(str(x))


def make(date, symbol, name, side, qty, price, fee=0):
    return Trade(
        trade_date=date, market='A', symbol=symbol, name=name, side=side,
        qty=D(qty), price=D(price), amount_local=D(qty) * D(price),
        currency='CNY', commission=D(fee),
    )


def test_baseline_regression():
    """合成基线回归：2024-02 月度收益必须 = 15,000.00 元"""
    trades = [
        make('2024-02-09', 'TEST_001', 'STOCK_A', 'BUY', 1000, '100.0'),
        make('2024-02-09', 'TEST_002', 'STOCK_B', 'BUY',  500, '200.0'),
        make('2024-02-18', 'TEST_003', 'STOCK_C', 'BUY', 2000, '50.0'),
        make('2024-02-18', 'TEST_002', 'STOCK_B', 'SELL', 500, '220.0'),
        make('2024-02-22', 'TEST_003', 'STOCK_C', 'SELL', 1000, '55.0'),
    ]
    events = weighted_avg(trades)
    sells = [e for e in events if e.trade.side == 'SELL']

    # STOCK_B：(220 - 200) × 500 = 10,000.00
    assert abs(sells[0].realized_pnl - D('10000.00')) < D('0.01'), \
        f"STOCK_B 收益错：{sells[0].realized_pnl}"
    # STOCK_C：(55 - 50) × 1000 = 5,000.00
    assert abs(sells[1].realized_pnl - D('5000.00')) < D('0.01'), \
        f"STOCK_C 收益错：{sells[1].realized_pnl}"

    total_pnl = sum((e.realized_pnl for e in sells), D(0))
    assert abs(total_pnl - D('15000.00')) < D('0.01'), \
        f"月度合计错：{total_pnl}"

    print("✅ test_baseline_regression 通过（月度合计 = 15,000.00）")


def test_weighted_avg_basic():
    """加权平均经典案例：1/15 买 100@10, 6/20 买 200@20, 12/10 卖 150@25"""
    trades = [
        make('2024-01-15', 'TEST', 'TEST', 'BUY', 100, 10),
        make('2024-06-20', 'TEST', 'TEST', 'BUY', 200, 20),
        make('2024-12-10', 'TEST', 'TEST', 'SELL', 150, 25),
    ]
    e = weighted_avg(trades)[-1]
    # avg = (1000 + 4000) / 300 = 16.666...
    # pnl = (25 - 16.666) × 150 = 1250
    assert abs(e.realized_pnl - D('1250')) < D('0.01'), f"基础 weighted_avg 错：{e.realized_pnl}"
    print("✅ test_weighted_avg_basic 通过")


def test_fifo_basic():
    """FIFO：同样数据 → pnl = 25×150 - (100×10 + 50×20) = 3750 - 2000 = 1750"""
    trades = [
        make('2024-01-15', 'TEST', 'TEST', 'BUY', 100, 10),
        make('2024-06-20', 'TEST', 'TEST', 'BUY', 200, 20),
        make('2024-12-10', 'TEST', 'TEST', 'SELL', 150, 25),
    ]
    e = fifo(trades)[-1]
    assert abs(e.realized_pnl - D('1750')) < D('0.01'), f"FIFO 错：{e.realized_pnl}"
    print("✅ test_fifo_basic 通过")


def test_oversell_raises():
    """卖出超持仓应抛错"""
    trades = [
        make('2024-01-15', 'TEST', 'TEST', 'BUY', 100, 10),
        make('2024-12-10', 'TEST', 'TEST', 'SELL', 200, 25),
    ]
    try:
        weighted_avg(trades)
        assert False, "应抛 ValueError"
    except ValueError:
        print("✅ test_oversell_raises 通过")


def test_avg_cost_unchanged_after_sell():
    """卖出后剩余持仓的平均成本不变"""
    trades = [
        make('2024-01-15', 'TEST', 'TEST', 'BUY', 100, 10),
        make('2024-06-20', 'TEST', 'TEST', 'BUY', 200, 20),
        make('2024-12-10', 'TEST', 'TEST', 'SELL', 150, 25),
    ]
    events = weighted_avg(trades)
    last = events[-1]
    # 卖前 avg = 16.666...，卖后剩 150 股，avg 仍应 = 16.666...
    expected_avg = (D('1000') + D('4000')) / D('300')
    assert abs(last.avg_cost_after - expected_avg) < D('0.001'), \
        f"卖出后 avg 错：{last.avg_cost_after} vs {expected_avg}"
    print("✅ test_avg_cost_unchanged_after_sell 通过")


def test_split_weighted_avg():
    """拆股 10:1 后卖出，加权平均"""
    from schema import CorporateAction
    trades = [
        make('2024-01-15', 'NVDA', 'NVIDIA', 'BUY', 20, 720),  # 总成本 14400
        # 拆股 10:1：20 股 → 200 股，单价 720 → 72
        CorporateAction(action_date='2024-06-10', market='A', symbol='NVDA',
                        name='NVIDIA', action_type='split',
                        ratio_num=D(10), ratio_den=D(1)),
        make('2024-12-05', 'NVDA', 'NVIDIA', 'SELL', 200, 138),  # 卖光
    ]
    e = weighted_avg(trades)[-1]
    # 卖前 avg = 14400/200 = 72；收益 = (138 - 72) × 200 = 13200
    assert abs(e.realized_pnl - D('13200')) < D('0.01'), f"拆股后 pnl 错: {e.realized_pnl}"
    print("✅ test_split_weighted_avg 通过")


def test_split_fifo():
    """拆股 10:1 后 FIFO 卖出"""
    from schema import CorporateAction
    trades = [
        make('2024-01-15', 'NVDA', 'NVIDIA', 'BUY', 20, 720),
        CorporateAction(action_date='2024-06-10', market='A', symbol='NVDA',
                        name='NVIDIA', action_type='split',
                        ratio_num=D(10), ratio_den=D(1)),
        make('2024-12-05', 'NVDA', 'NVIDIA', 'SELL', 200, 138),
    ]
    e = fifo(trades)[-1]
    # FIFO: 唯一一批 200 股 @72，卖出 200 股 @138 → 收益 = (138 − 72) × 200 = 13200
    assert abs(e.realized_pnl - D('13200')) < D('0.01'), f"FIFO 拆股后 pnl 错: {e.realized_pnl}"
    print("✅ test_split_fifo 通过")


def test_bonus_share():
    """送股 10 送 3：原 100 股变 130 股，总成本不变"""
    from schema import CorporateAction
    trades = [
        make('2024-01-15', 'TEST', 'TEST', 'BUY', 100, 10),  # 总成本 1000
        CorporateAction(action_date='2024-06-01', market='A', symbol='TEST',
                        name='TEST', action_type='bonus',
                        bonus_ratio=D('0.3')),  # 每股送 0.3 股
        make('2024-12-01', 'TEST', 'TEST', 'SELL', 130, 12),  # 卖光
    ]
    e = weighted_avg(trades)[-1]
    # 130 股总成本仍 1000；avg ≈ 7.692；收益 = (12 - 7.692) × 130 = 560
    assert abs(e.qty_after - D(0)) < D('0.01')
    expected_pnl = D(12) * D(130) - D(1000)  # 1560 - 1000 = 560
    assert abs(e.realized_pnl - expected_pnl) < D('0.01'), f"送股后 pnl 错: {e.realized_pnl}"
    print("✅ test_bonus_share 通过")


def test_rights_issue():
    """配股 1:5（每 5 股可配 1 股）@配股价 8"""
    from schema import CorporateAction
    trades = [
        make('2024-01-15', 'TEST', 'TEST', 'BUY', 100, 10),  # 总成本 1000
        CorporateAction(action_date='2024-06-01', market='A', symbol='TEST',
                        name='TEST', action_type='rights',
                        rights_ratio=D('0.2'),    # 每股配 0.2 股 = 1:5
                        subscription_price=D(8)),  # 配股价 8
        make('2024-12-01', 'TEST', 'TEST', 'SELL', 120, 12),
    ]
    e = weighted_avg(trades)[-1]
    # 配股后：120 股，总成本 = 1000 + 20×8 = 1160；avg = 9.667
    # 收益 = (12 - 9.667) × 120 = 280
    expected_pnl = D(12) * D(120) - (D(1000) + D(20) * D(8))
    assert abs(e.realized_pnl - expected_pnl) < D('0.01'), f"配股后 pnl 错: {e.realized_pnl}"
    print("✅ test_rights_issue 通过")


def test_reverse_split():
    """合股 10:1：100 股 @1 → 10 股 @10"""
    from schema import CorporateAction
    trades = [
        make('2024-01-15', 'TEST', 'TEST', 'BUY', 100, 1),  # 总成本 100
        CorporateAction(action_date='2024-06-01', market='A', symbol='TEST',
                        name='TEST', action_type='reverse_split',
                        ratio_num=D(1), ratio_den=D(10)),
        make('2024-12-01', 'TEST', 'TEST', 'SELL', 10, 15),
    ]
    e = weighted_avg(trades)[-1]
    # 合股后：10 股，总成本仍 100；avg = 10；收益 = (15 - 10) × 10 = 50
    assert abs(e.realized_pnl - D(50)) < D('0.01'), f"合股后 pnl 错: {e.realized_pnl}"
    print("✅ test_reverse_split 通过")


def test_internal_transfer():
    """同券商内部转仓：成本不变，转出 + 转入配对（统一 account='broker_a'）"""
    from schema import Transfer
    trades = [
        Trade(trade_date='2024-01-15', market='A', symbol='TEST', name='TEST',
              side='BUY', qty=D(100), price=D(10), amount_local=D(1000),
              currency='CNY', account='broker_a'),
        Transfer(transfer_date='2024-06-01', market='A', symbol='TEST',
                 name='TEST', qty=D(100), direction='Out',
                 from_account='broker_a', to_account='broker_a'),
        Transfer(transfer_date='2024-06-01', market='A', symbol='TEST',
                 name='TEST', qty=D(100), direction='In',
                 from_account='broker_a', to_account='broker_a'),
        Trade(trade_date='2024-12-01', market='A', symbol='TEST', name='TEST',
              side='SELL', qty=D(100), price=D(15), amount_local=D(1500),
              currency='CNY', account='broker_a'),
    ]
    e = weighted_avg(trades)[-1]
    # 转仓不改变成本；卖出 100 股 @15，原成本 1000；收益 = 500
    assert abs(e.realized_pnl - D(500)) < D('0.01'), f"内部转仓后 pnl 错: {e.realized_pnl}"
    print("✅ test_internal_transfer 通过")


def test_cross_broker_transfer():
    """跨券商转入：用户提供原值结转单"""
    from schema import CrossBrokerTransfer
    trades = [
        CrossBrokerTransfer(transfer_date='2024-01-01', market='HK', symbol='TEST_HK',
                            name='TEST_HK', qty=D(100), cost_basis_local=D(30000),
                            currency='HKD', from_broker='broker_b',
                            to_account='broker_a'),
    ]
    sell = Trade(trade_date='2024-12-01', market='HK', symbol='TEST_HK', name='TEST_HK',
                 side='SELL', qty=D(100), price=D(400), amount_local=D(40000),
                 currency='HKD', account='broker_a')
    trades.append(sell)
    e = weighted_avg(trades)[-1]
    # 收益 = 100 × 400 - 30000 = 10000
    assert abs(e.realized_pnl - D(10000)) < D('0.01'), f"跨券商转入后 pnl 错: {e.realized_pnl}"
    print("✅ test_cross_broker_transfer 通过")


if __name__ == '__main__':
    test_baseline_regression()
    test_weighted_avg_basic()
    test_fifo_basic()
    test_oversell_raises()
    test_avg_cost_unchanged_after_sell()
    test_split_weighted_avg()
    test_split_fifo()
    test_bonus_share()
    test_rights_issue()
    test_reverse_split()
    test_internal_transfer()
    test_cross_broker_transfer()
    print("\n🎉 所有单元测试通过（含 P0+P1：拆股/合股/送股/配股/转仓/跨券商）")
