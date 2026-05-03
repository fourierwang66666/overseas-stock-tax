"""
长桥月结单 PDF 解析器

PDF 密码默认规则：手机号末4位 + 开户证件号末4位（连续 8 位数字）

用法：
    python3 parse_longbridge.py <input.pdf> [--password XXXX] [--out trades.json]
"""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import List

try:
    import pdfplumber  # type: ignore
except ImportError:
    pdfplumber = None

sys.path.insert(0, str(Path(__file__).parent))
from cost_basis import Trade
from parse_futu import _to_decimal, _row_to_trade  # 复用


LONGBRIDGE_FIELD_MAP = {
    'trade_date': ['成交日期', '交收日', 'Trade Date', 'Settlement Date'],
    'market': ['市场', 'Market', '交易所'],
    'symbol': ['代码', 'Symbol', 'Code', '股票代码'],
    'name': ['名称', 'Name', '股票名称'],
    'side': ['方向', 'Side', '买卖'],
    'qty': ['数量', 'Quantity', 'Qty'],
    'price': ['价格', 'Price', '成交价'],
    'amount_local': ['金额', 'Amount', '成交金额'],
    'currency': ['货币', 'Currency'],
    'commission': ['佣金', 'Commission'],
    'platform_fee': ['平台费', 'Platform Fee'],
    'stamp_duty': ['印花税', 'Stamp Duty'],
    'settlement_fee': ['结算费', 'Settlement Fee'],
    'sec_fee': ['SEC费', 'SEC Fee', 'FINRA'],
    'other_fee': ['交易征费', '征费'],
}


def _is_longbridge_trade_header(header: List[str]) -> bool:
    flat = ' '.join(header)
    return ('成交' in flat or 'Trade' in flat or '交收' in flat) and \
           ('数量' in flat or 'Qty' in flat)


def _build_lb_column_index(header: List[str]) -> dict:
    idx = {}
    for key, names in LONGBRIDGE_FIELD_MAP.items():
        for col_i, col in enumerate(header):
            if any(n in col for n in names):
                idx[key] = col_i
                break
    return idx


def parse_longbridge_pdf(path: str, password: str = '') -> List[Trade]:
    if pdfplumber is None:
        raise RuntimeError("需要安装 pdfplumber：pip install pdfplumber")

    trades: List[Trade] = []
    with pdfplumber.open(path, password=password) as pdf:
        first_page_text = pdf.pages[0].extract_text() if pdf.pages else ''
        if not first_page_text or len(first_page_text.strip()) < 20:
            raise ValueError(
                f"{path} 看起来是扫描件（无文本层）。skill 禁止视觉识别。"
                "请用 OCR 工具或导出其他格式。"
            )

        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if not table or not table[0]:
                    continue
                header = [str(c or '').strip() for c in table[0]]
                if not _is_longbridge_trade_header(header):
                    continue
                col_idx = _build_lb_column_index(header)
                for row in table[1:]:
                    if not row or not any(row):
                        continue
                    try:
                        # 长桥列 mapping 与富途略不同，但解析逻辑相同
                        # 用 monkey-patch 字段映射在调用前覆盖（简化处理直接 inline）
                        t = _parse_lb_row(row, col_idx)
                        if t:
                            trades.append(t)
                    except Exception as e:
                        print(f"[WARN] 长桥行解析失败：{row}，原因：{e}", file=sys.stderr)
    return trades


def _parse_lb_row(row, col_idx: dict) -> Trade:
    def get(key, default=''):
        i = col_idx.get(key)
        if i is None or i >= len(row):
            return default
        return row[i]

    side_raw = str(get('side', '')).upper().strip()
    if 'BUY' in side_raw or '买' in side_raw or side_raw == 'B':
        side = 'BUY'
    elif 'SELL' in side_raw or '卖' in side_raw or side_raw == 'S':
        side = 'SELL'
    else:
        return None

    qty = _to_decimal(get('qty'))
    price = _to_decimal(get('price'))
    if qty == 0 or price == 0:
        return None

    symbol = str(get('symbol', '')).strip()
    market_raw = str(get('market', '')).strip().upper()
    if 'HK' in market_raw or '港' in market_raw:
        market = 'HK'
        symbol = symbol.zfill(5)
    elif 'US' in market_raw or '美' in market_raw:
        market = 'US'
    elif 'SG' in market_raw or '新加坡' in market_raw:
        market = 'SG'
    else:
        market = market_raw or 'UNKNOWN'

    return Trade(
        trade_date=str(get('trade_date', ''))[:10],
        market=market,
        symbol=symbol,
        name=str(get('name', '')).strip(),
        side=side,
        qty=qty,
        price=price,
        amount_local=_to_decimal(get('amount_local')) or qty * price,
        currency=str(get('currency', 'HKD')).strip().upper(),
        commission=_to_decimal(get('commission')),
        platform_fee=_to_decimal(get('platform_fee')),
        stamp_duty=_to_decimal(get('stamp_duty')),
        settlement_fee=_to_decimal(get('settlement_fee')),
        sec_fee=_to_decimal(get('sec_fee')),
        other_fee=_to_decimal(get('other_fee')),
        account='longbridge',
    )


def main():
    p = argparse.ArgumentParser(description='长桥月结单解析器')
    p.add_argument('input', help='长桥 PDF 路径')
    p.add_argument('--password', default='', help='PDF 密码（默认 = 手机末4 + 证件末4）')
    p.add_argument('--out', default='-', help='输出 JSON 路径，- 为 stdout')
    args = p.parse_args()

    trades = parse_longbridge_pdf(args.input, args.password)
    print(f"[OK] 解析 {args.input}：{len(trades)} 笔交易", file=sys.stderr)

    out = [
        {**t.__dict__, 'qty': str(t.qty), 'price': str(t.price),
         'amount_local': str(t.amount_local),
         'commission': str(t.commission), 'platform_fee': str(t.platform_fee),
         'stamp_duty': str(t.stamp_duty), 'settlement_fee': str(t.settlement_fee),
         'sec_fee': str(t.sec_fee), 'other_fee': str(t.other_fee)}
        for t in trades
    ]
    if args.out == '-':
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
