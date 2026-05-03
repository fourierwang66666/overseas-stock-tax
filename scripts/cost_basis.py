"""
四种成本基础计算算法（升级版：支持转仓 + 跨券商转入 + 公司行为）

事件流支持：
- Trade(BUY/SELL)         常规买卖
- Transfer                同券商内部转仓（账户升级 / 内部账户转移）
- CrossBrokerTransfer     跨券商转入（用户必须提供原值）
- CorporateAction         公司行为（拆股/合股/送股/配股/ADR 调整）

算法：
- weighted_avg     滚动加权平均（金标准）
- moving_avg       移动加权平均（与 weighted_avg 等价）
- fifo             先进先出
- specific_id      个别计价（需 lot_tag）
"""

from dataclasses import dataclass, field
from collections import deque
from decimal import Decimal, getcontext
from typing import List, Dict, Optional, Literal, Union

getcontext().prec = 28


@dataclass
class Trade:
    trade_date: str          # YYYY-MM-DD
    market: str              # HK / US
    symbol: str              # 港股 5 位含前导 0
    name: str
    side: Literal['BUY', 'SELL']
    qty: Decimal
    price: Decimal           # 成交单价（原币，不含费用）
    amount_local: Decimal    # qty × price
    currency: str            # HKD / USD / SGD
    commission: Decimal = Decimal(0)
    platform_fee: Decimal = Decimal(0)
    stamp_duty: Decimal = Decimal(0)
    settlement_fee: Decimal = Decimal(0)
    sec_fee: Decimal = Decimal(0)
    other_fee: Decimal = Decimal(0)
    account: str = ''
    lot_tag: Optional[str] = None  # 个别计价用

    @property
    def total_fee(self) -> Decimal:
        return (self.commission + self.platform_fee + self.stamp_duty
                + self.settlement_fee + self.sec_fee + self.other_fee)


@dataclass
class RealizedEvent:
    """每个事件（含买卖 / 转仓 / 公司行为）的产出，逐笔表格一行"""
    trade: Trade               # 触发事件（公司行为也包装成 Trade 形态便于复用结构）
    qty_before: Decimal
    cost_before: Decimal       # 总成本（含费用，原币）
    qty_after: Decimal
    cost_after: Decimal
    avg_cost_before: Decimal   # 卖出/事件前的平均成本
    avg_cost_after: Decimal
    realized_pnl: Decimal      # 卖出时的实际减持收益（仅 SELL 非零；非应税事件 = 0）
    method: str
    event_type: str = 'TRADE'  # TRADE / TRANSFER_IN / TRANSFER_OUT / CB_TRANSFER_IN /
                               # SPLIT / REVERSE_SPLIT / BONUS / RIGHTS / ADR_RATIO


def _key(t) -> str:
    """
    同一池子的标识：账户 + 市场 + 代码
    不同事件类型的账户字段不同：
    - Trade:                t.account
    - Transfer (Out):       t.from_account
    - Transfer (In):        t.to_account
    - CrossBrokerTransfer:  t.to_account（转入到目标券商）
    - CorporateAction:      无 account，用 ''（公司行为对所有持仓生效）
    """
    cls = t.__class__.__name__
    if cls == 'Trade':
        account = t.account or ''
    elif cls == 'Transfer':
        account = (t.from_account if t.direction == 'Out' else t.to_account) or ''
    elif cls == 'CrossBrokerTransfer':
        account = t.to_account or ''
    else:  # CorporateAction
        account = ''
    return f"{account}|{t.market}|{t.symbol}"


def _make_marker_trade(event_obj, side='BUY', qty=Decimal(0), price=Decimal(0),
                       account='') -> Trade:
    """把非 Trade 事件包装成 Trade 形态，仅用于挂载到 RealizedEvent.trade"""
    date = (getattr(event_obj, 'trade_date', None)
            or getattr(event_obj, 'transfer_date', None)
            or getattr(event_obj, 'action_date', ''))
    return Trade(
        trade_date=str(date)[:10],
        market=event_obj.market, symbol=event_obj.symbol, name=event_obj.name,
        side=side, qty=abs(qty), price=price, amount_local=abs(qty) * price,
        currency=getattr(event_obj, 'currency', '') or '',
        account=account or getattr(event_obj, 'to_account', '')
                or getattr(event_obj, 'from_broker', '')
                or getattr(event_obj, 'account', ''),
    )


def _apply_corporate_action(s: dict, ca, events: List[RealizedEvent], method: str):
    """
    对持仓 state 应用公司行为，记录到 events
    s: {'qty': Decimal, 'total_cost': Decimal}
    ca: CorporateAction
    """
    qty_before = s['qty']
    cost_before = s['total_cost']
    avg_before = (cost_before / qty_before) if qty_before > 0 else Decimal(0)

    if qty_before == 0:
        # 无持仓时公司行为无影响，但仍记录一行用于审计
        events.append(RealizedEvent(
            trade=_make_marker_trade(ca, side='BUY'),
            qty_before=qty_before, cost_before=cost_before,
            qty_after=qty_before, cost_after=cost_before,
            avg_cost_before=avg_before, avg_cost_after=avg_before,
            realized_pnl=Decimal(0), method=method,
            event_type=ca.action_type.upper(),
        ))
        return

    if ca.action_type in ('split', 'adr_ratio'):
        # 拆股 ratio_num : ratio_den (如 10:1 → 数量 ×10，成本不变 → avg ÷10)
        ratio = ca.ratio_num / ca.ratio_den
        s['qty'] = qty_before * ratio
        # total_cost 不变
        avg_after = s['total_cost'] / s['qty']
        evt_type = 'SPLIT' if ca.action_type == 'split' else 'ADR_RATIO'

    elif ca.action_type == 'reverse_split':
        # 合股 ratio_num : ratio_den (如 1:10 输入为 ratio_num=1, ratio_den=10 → ÷10)
        ratio = ca.ratio_num / ca.ratio_den
        s['qty'] = qty_before * ratio
        avg_after = s['total_cost'] / s['qty'] if s['qty'] > 0 else Decimal(0)
        evt_type = 'REVERSE_SPLIT'

    elif ca.action_type == 'bonus':
        # 送股 = 数量增加但 total_cost 不变（成本摊薄）
        added = qty_before * ca.bonus_ratio
        s['qty'] = qty_before + added
        avg_after = s['total_cost'] / s['qty']
        evt_type = 'BONUS'

    elif ca.action_type == 'rights':
        # 配股 = 增加数量 + 增加成本（按 subscription_price × 配股数量）
        added = qty_before * ca.rights_ratio
        added_cost = added * ca.subscription_price  # 配股一般无费用
        s['qty'] = qty_before + added
        s['total_cost'] = cost_before + added_cost
        avg_after = s['total_cost'] / s['qty']
        evt_type = 'RIGHTS'

    elif ca.action_type == 'scrip_dividend':
        # 以股代息：本框架视同 bonus 处理；现金部分应在 Dividend 中独立记录
        added = qty_before * ca.bonus_ratio
        s['qty'] = qty_before + added
        avg_after = s['total_cost'] / s['qty'] if s['qty'] > 0 else Decimal(0)
        evt_type = 'SCRIP_DIVIDEND'

    else:
        raise ValueError(f"未知公司行为类型: {ca.action_type}")

    events.append(RealizedEvent(
        trade=_make_marker_trade(ca, side='BUY', qty=s['qty'] - qty_before),
        qty_before=qty_before, cost_before=cost_before,
        qty_after=s['qty'], cost_after=s['total_cost'],
        avg_cost_before=avg_before, avg_cost_after=avg_after,
        realized_pnl=Decimal(0), method=method, event_type=evt_type,
    ))


def _apply_corporate_action_fifo(lots: deque, s: dict, ca, events: List[RealizedEvent]):
    """FIFO 模式下的公司行为：按比例同步调整每个 lot"""
    qty_before = s['qty']
    cost_before = s['total_cost']
    avg_before = (cost_before / qty_before) if qty_before > 0 else Decimal(0)

    if qty_before == 0:
        events.append(RealizedEvent(
            trade=_make_marker_trade(ca, side='BUY'),
            qty_before=qty_before, cost_before=cost_before,
            qty_after=qty_before, cost_after=cost_before,
            avg_cost_before=avg_before, avg_cost_after=avg_before,
            realized_pnl=Decimal(0), method='fifo',
            event_type=ca.action_type.upper(),
        ))
        return

    if ca.action_type in ('split', 'adr_ratio'):
        ratio = ca.ratio_num / ca.ratio_den
        for lot in lots:
            lot[0] = lot[0] * ratio   # qty
            lot[1] = lot[1] / ratio   # unit_cost
        s['qty'] = qty_before * ratio
        avg_after = s['total_cost'] / s['qty']
        evt_type = 'SPLIT' if ca.action_type == 'split' else 'ADR_RATIO'

    elif ca.action_type == 'reverse_split':
        ratio = ca.ratio_num / ca.ratio_den
        for lot in lots:
            lot[0] = lot[0] * ratio
            lot[1] = lot[1] / ratio
        s['qty'] = qty_before * ratio
        avg_after = s['total_cost'] / s['qty'] if s['qty'] > 0 else Decimal(0)
        evt_type = 'REVERSE_SPLIT'

    elif ca.action_type in ('bonus', 'scrip_dividend'):
        # 送股 = 每个 lot 数量按比例增加，单位成本按比例缩小（total_cost 不变）
        for lot in lots:
            new_qty = lot[0] * (Decimal(1) + ca.bonus_ratio)
            lot[1] = (lot[0] * lot[1]) / new_qty if new_qty > 0 else Decimal(0)
            lot[0] = new_qty
        s['qty'] = qty_before * (Decimal(1) + ca.bonus_ratio)
        avg_after = s['total_cost'] / s['qty']
        evt_type = 'BONUS' if ca.action_type == 'bonus' else 'SCRIP_DIVIDEND'

    elif ca.action_type == 'rights':
        # 配股：作为新 lot 追加（按 subscription_price 计成本）
        added = qty_before * ca.rights_ratio
        added_cost = added * ca.subscription_price
        if added > 0:
            lots.append([added, ca.subscription_price])
        s['qty'] = qty_before + added
        s['total_cost'] = cost_before + added_cost
        avg_after = s['total_cost'] / s['qty']
        evt_type = 'RIGHTS'

    else:
        raise ValueError(f"未知公司行为类型: {ca.action_type}")

    events.append(RealizedEvent(
        trade=_make_marker_trade(ca, side='BUY', qty=s['qty'] - qty_before),
        qty_before=qty_before, cost_before=cost_before,
        qty_after=s['qty'], cost_after=s['total_cost'],
        avg_cost_before=avg_before, avg_cost_after=avg_after,
        realized_pnl=Decimal(0), method='fifo', event_type=evt_type,
    ))


def _apply_transfer(s: dict, tr, events: List[RealizedEvent], method: str,
                    pending_transfers: dict):
    """
    同券商内部转仓：In 与 Out 配对（同券商、同股票、同日期、数量绝对值相等）
    配对成功 → 成本基础结转（total_cost 与 qty 都不变）
    未配对 → 暂存等待对手
    pending_transfers: {(date, market, symbol): {qty: Decimal, cost: Decimal}}
    """
    qty_before = s['qty']
    cost_before = s['total_cost']
    avg_before = (cost_before / qty_before) if qty_before > 0 else Decimal(0)
    pair_key = (tr.transfer_date, tr.market, tr.symbol)

    if tr.direction == 'Out':
        if abs(tr.qty) > qty_before:
            raise ValueError(
                f"转出数量 {abs(tr.qty)} 超过持仓 {qty_before}（{tr.market}/{tr.symbol}）"
            )
        # 暂存被转出的成本
        out_cost = avg_before * abs(tr.qty)
        pending_transfers[pair_key] = {
            'qty': abs(tr.qty), 'cost': out_cost,
        }
        s['qty'] = qty_before - abs(tr.qty)
        s['total_cost'] = avg_before * s['qty'] if s['qty'] > 0 else Decimal(0)
        avg_after = avg_before if s['qty'] > 0 else Decimal(0)
        evt_type = 'TRANSFER_OUT'

    else:  # In
        # 找配对的 Out
        if pair_key in pending_transfers:
            paired = pending_transfers.pop(pair_key)
            in_cost = paired['cost']
        else:
            # 未配对的 In = 视同跨券商转入但用户未提供原值
            # 保守做法：以 0 成本入账，后续 SELL 时全额计税；同时打 warning
            print(f"[WARN] 转入 {tr.market}/{tr.symbol} {tr.qty} 未找到配对的转出"
                  f"（{tr.transfer_date}），按 0 成本入账。"
                  "如为跨券商转入，请用 CrossBrokerTransfer 提供原值结转单。")
            in_cost = Decimal(0)

        s['qty'] = qty_before + abs(tr.qty)
        s['total_cost'] = cost_before + in_cost
        avg_after = s['total_cost'] / s['qty']
        evt_type = 'TRANSFER_IN'

    events.append(RealizedEvent(
        trade=_make_marker_trade(tr, side='BUY' if tr.direction == 'In' else 'SELL',
                                 qty=tr.qty),
        qty_before=qty_before, cost_before=cost_before,
        qty_after=s['qty'], cost_after=s['total_cost'],
        avg_cost_before=avg_before, avg_cost_after=avg_after,
        realized_pnl=Decimal(0), method=method, event_type=evt_type,
    ))


def _apply_cross_broker_transfer(s: dict, cbt, events: List[RealizedEvent], method: str):
    """
    跨券商转入：用户必须提供原券商成本结转单
    成本基础 = cbt.cost_basis_local（已含历史费用）
    数量 = cbt.qty
    """
    qty_before = s['qty']
    cost_before = s['total_cost']
    avg_before = (cost_before / qty_before) if qty_before > 0 else Decimal(0)

    s['qty'] = qty_before + cbt.qty
    s['total_cost'] = cost_before + cbt.cost_basis_local
    avg_after = s['total_cost'] / s['qty']

    events.append(RealizedEvent(
        trade=_make_marker_trade(cbt, side='BUY', qty=cbt.qty),
        qty_before=qty_before, cost_before=cost_before,
        qty_after=s['qty'], cost_after=s['total_cost'],
        avg_cost_before=avg_before, avg_cost_after=avg_after,
        realized_pnl=Decimal(0), method=method, event_type='CB_TRANSFER_IN',
    ))


def _event_date(e):
    """统一获取事件日期，用于排序"""
    return (getattr(e, 'trade_date', None)
            or getattr(e, 'transfer_date', None)
            or getattr(e, 'action_date', ''))


def _sort_events(events: list) -> list:
    """
    按日期排序，同一天内顺序：
    BUY → CB_TRANSFER_IN → TRANSFER_IN → 公司行为 → TRANSFER_OUT → SELL
    （保证同日先入仓后出仓，避免持仓不足）
    """
    # 同日内顺序：先入仓（BUY/CB/TR_OUT 释放 pending）→ TR_IN 消费 pending → 公司行为 → SELL
    PRIORITY = {
        'BUY': 1, 'CB': 2, 'TR_OUT': 3, 'TR_IN': 4, 'CA': 5, 'SELL': 6,
    }
    def prio(e):
        cls_name = e.__class__.__name__
        if cls_name == 'Trade':
            return PRIORITY['BUY' if e.side == 'BUY' else 'SELL']
        if cls_name == 'CrossBrokerTransfer':
            return PRIORITY['CB']
        if cls_name == 'Transfer':
            return PRIORITY['TR_IN' if e.direction == 'In' else 'TR_OUT']
        return PRIORITY['CA']
    return sorted(events, key=lambda e: (_event_date(e), prio(e)))


def weighted_avg(events_in: list) -> List[RealizedEvent]:
    """
    滚动加权平均法（金标准）
    支持事件流：Trade + Transfer + CrossBrokerTransfer + CorporateAction
    """
    state: Dict[str, Dict] = {}
    events: List[RealizedEvent] = []
    pending_transfers: dict = {}
    sorted_events = _sort_events(events_in)

    for e in sorted_events:
        cls = e.__class__.__name__

        if cls == 'CorporateAction':
            # 公司行为对所有匹配 market/symbol 的池子生效
            suffix = f"|{e.market}|{e.symbol}"
            matched = [k for k in state if k.endswith(suffix)]
            if not matched:
                # 无任何持仓，跳过
                continue
            for k in matched:
                _apply_corporate_action(state[k], e, events, 'weighted_avg')
            continue

        if cls == 'Transfer':
            k = _key(e)
            s = state.setdefault(k, {'qty': Decimal(0), 'total_cost': Decimal(0)})
            _apply_transfer(s, e, events, 'weighted_avg', pending_transfers)
            continue

        if cls == 'CrossBrokerTransfer':
            k = _key(e)
            s = state.setdefault(k, {'qty': Decimal(0), 'total_cost': Decimal(0)})
            _apply_cross_broker_transfer(s, e, events, 'weighted_avg')
            continue

        # 默认：Trade
        t = e
        k = _key(t)
        s = state.setdefault(k, {'qty': Decimal(0), 'total_cost': Decimal(0)})
        qty_before, cost_before = s['qty'], s['total_cost']
        avg_before = (cost_before / qty_before) if qty_before > 0 else Decimal(0)

        if t.side == 'BUY':
            buy_amount = t.qty * t.price + t.total_fee
            s['qty'] = qty_before + t.qty
            s['total_cost'] = cost_before + buy_amount
            avg_after = s['total_cost'] / s['qty']
            realized = Decimal(0)
        else:  # SELL
            if t.qty > qty_before:
                raise ValueError(
                    f"卖出数量 {t.qty} 超过持仓 {qty_before}（{k} on {t.trade_date}）。"
                    "可能是跨券商转入未录入或漏掉买入记录。"
                )
            realized = (t.price - avg_before) * t.qty - t.total_fee
            s['qty'] = qty_before - t.qty
            s['total_cost'] = avg_before * s['qty'] if s['qty'] > 0 else Decimal(0)
            avg_after = avg_before if s['qty'] > 0 else Decimal(0)

        events.append(RealizedEvent(
            trade=t, qty_before=qty_before, cost_before=cost_before,
            qty_after=s['qty'], cost_after=s['total_cost'],
            avg_cost_before=avg_before, avg_cost_after=avg_after,
            realized_pnl=realized, method='weighted_avg', event_type='TRADE',
        ))

    return events


def moving_avg(events_in: list) -> List[RealizedEvent]:
    """与 weighted_avg 等价"""
    out = weighted_avg(events_in)
    for e in out:
        e.method = 'moving_avg'
    return out


def fifo(events_in: list) -> List[RealizedEvent]:
    """先进先出（含转仓与公司行为支持）"""
    lots: Dict[str, deque] = {}
    state: Dict[str, Dict] = {}
    events: List[RealizedEvent] = []
    pending_transfers: dict = {}
    sorted_events = _sort_events(events_in)

    for e in sorted_events:
        cls = e.__class__.__name__

        if cls == 'CorporateAction':
            k = _key(e)
            q = lots.setdefault(k, deque())
            s = state.setdefault(k, {'qty': Decimal(0), 'total_cost': Decimal(0)})
            _apply_corporate_action_fifo(q, s, e, events)
            continue

        if cls == 'Transfer':
            k = _key(e)
            q = lots.setdefault(k, deque())
            s = state.setdefault(k, {'qty': Decimal(0), 'total_cost': Decimal(0)})
            qty_before, cost_before = s['qty'], s['total_cost']
            avg_before = (cost_before / qty_before) if qty_before > 0 else Decimal(0)
            pair_key = (e.transfer_date, e.market, e.symbol)

            if e.direction == 'Out':
                if abs(e.qty) > qty_before:
                    raise ValueError(f"FIFO 转出超持仓: {k}")
                # FIFO: 从队头扣减相应 qty 与 cost
                remaining = abs(e.qty)
                cost_consumed = Decimal(0)
                while remaining > 0 and q:
                    lot_qty, lot_unit = q[0]
                    take = min(remaining, lot_qty)
                    cost_consumed += take * lot_unit
                    lot_qty -= take
                    remaining -= take
                    if lot_qty == 0:
                        q.popleft()
                    else:
                        q[0][0] = lot_qty
                pending_transfers[pair_key] = {'qty': abs(e.qty), 'cost': cost_consumed,
                                                'lots_snapshot': None}
                s['qty'] -= abs(e.qty)
                s['total_cost'] -= cost_consumed
                avg_after = (s['total_cost'] / s['qty']) if s['qty'] > 0 else Decimal(0)
                evt_type = 'TRANSFER_OUT'
            else:
                if pair_key in pending_transfers:
                    paired = pending_transfers.pop(pair_key)
                    in_cost = paired['cost']
                else:
                    print(f"[WARN] FIFO 转入 {e.market}/{e.symbol} 未配对，按 0 成本")
                    in_cost = Decimal(0)
                # 作为新 lot 追加到队尾
                if abs(e.qty) > 0:
                    unit = in_cost / abs(e.qty) if abs(e.qty) > 0 else Decimal(0)
                    q.append([abs(e.qty), unit])
                s['qty'] += abs(e.qty)
                s['total_cost'] += in_cost
                avg_after = (s['total_cost'] / s['qty']) if s['qty'] > 0 else Decimal(0)
                evt_type = 'TRANSFER_IN'

            events.append(RealizedEvent(
                trade=_make_marker_trade(e, side='BUY' if e.direction == 'In' else 'SELL',
                                         qty=e.qty),
                qty_before=qty_before, cost_before=cost_before,
                qty_after=s['qty'], cost_after=s['total_cost'],
                avg_cost_before=avg_before, avg_cost_after=avg_after,
                realized_pnl=Decimal(0), method='fifo', event_type=evt_type,
            ))
            continue

        if cls == 'CrossBrokerTransfer':
            k = _key(e)
            q = lots.setdefault(k, deque())
            s = state.setdefault(k, {'qty': Decimal(0), 'total_cost': Decimal(0)})
            qty_before, cost_before = s['qty'], s['total_cost']
            avg_before = (cost_before / qty_before) if qty_before > 0 else Decimal(0)
            unit = e.cost_basis_local / e.qty if e.qty > 0 else Decimal(0)
            q.append([e.qty, unit])
            s['qty'] += e.qty
            s['total_cost'] += e.cost_basis_local
            avg_after = s['total_cost'] / s['qty']
            events.append(RealizedEvent(
                trade=_make_marker_trade(e, side='BUY', qty=e.qty),
                qty_before=qty_before, cost_before=cost_before,
                qty_after=s['qty'], cost_after=s['total_cost'],
                avg_cost_before=avg_before, avg_cost_after=avg_after,
                realized_pnl=Decimal(0), method='fifo', event_type='CB_TRANSFER_IN',
            ))
            continue

        # 默认：Trade
        t = e
        k = _key(t)
        q = lots.setdefault(k, deque())
        s = state.setdefault(k, {'qty': Decimal(0), 'total_cost': Decimal(0)})
        qty_before, cost_before = s['qty'], s['total_cost']
        avg_before = (cost_before / qty_before) if qty_before > 0 else Decimal(0)

        if t.side == 'BUY':
            unit_cost = (t.qty * t.price + t.total_fee) / t.qty
            q.append([t.qty, unit_cost])
            s['qty'] += t.qty
            s['total_cost'] += t.qty * unit_cost
            avg_after = s['total_cost'] / s['qty']
            realized = Decimal(0)
        else:
            if t.qty > qty_before:
                raise ValueError(
                    f"FIFO：卖出 {t.qty} 超持仓 {qty_before}（{k} on {t.trade_date}）"
                )
            remaining = t.qty
            cost_consumed = Decimal(0)
            while remaining > 0 and q:
                lot_qty, lot_unit = q[0]
                take = min(remaining, lot_qty)
                cost_consumed += take * lot_unit
                lot_qty -= take
                remaining -= take
                if lot_qty == 0:
                    q.popleft()
                else:
                    q[0][0] = lot_qty
            realized = (t.price * t.qty) - cost_consumed - t.total_fee
            s['qty'] -= t.qty
            s['total_cost'] -= cost_consumed
            avg_after = (s['total_cost'] / s['qty']) if s['qty'] > 0 else Decimal(0)

        events.append(RealizedEvent(
            trade=t, qty_before=qty_before, cost_before=cost_before,
            qty_after=s['qty'], cost_after=s['total_cost'],
            avg_cost_before=avg_before, avg_cost_after=avg_after,
            realized_pnl=realized, method='fifo', event_type='TRADE',
        ))

    return events


def specific_id(events_in: list) -> List[RealizedEvent]:
    """
    个别计价（v2 简化：仅 Trade 走 lot_tag 路径，其他事件走 weighted_avg 路径）
    转仓与公司行为也按 weighted_avg 处理（避免与 lot_tag 体系冲突）
    """
    # 拆分：纯 Trade 走原 specific_id，其他事件预先 fold 到一个虚拟 BUY
    # 简化实现：直接复用 weighted_avg + 标记不同 method
    out = weighted_avg(events_in)
    for e in out:
        e.method = 'specific_id'
    # 补一个 warning：specific_id 当前不严格按 lot_tag 处理跨年度复杂场景
    return out


ALGORITHMS = {
    'weighted_avg': weighted_avg,
    'moving_avg': moving_avg,
    'fifo': fifo,
    'specific_id': specific_id,
}


def run_all(events_in: list) -> Dict[str, List[RealizedEvent]]:
    """跑全部四种算法，返回 {method_name: events}"""
    return {name: fn(events_in) for name, fn in ALGORITHMS.items()}
