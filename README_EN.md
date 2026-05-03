# overseas-stock-tax

> Individual income tax filing tool for Chinese tax residents trading overseas stocks (HK / US via Futu / Longbridge).
>
> Compatible with **Claude Code · OpenAI Codex CLI · Cursor**, plus a browser-only web app.

[中文](./README.md) · English

> 🌐 **Live demo**: [https://fourierwang66666.github.io/overseas-stock-tax/](https://fourierwang66666.github.io/overseas-stock-tax/) — runs Pyodide in your browser; **your data never leaves your machine**.

## Multi-IDE / Agent Support

| Environment | Entry file | How to trigger |
|---|---|---|
| **Claude Code** | `SKILL.md` | clone into `~/.claude/skills/overseas-stock-tax/`; mention "海外股票个税" or "overseas stock tax" |
| **OpenAI Codex CLI** | `AGENTS.md` | clone the repo; `codex` reads `AGENTS.md` automatically |
| **Cursor** | `.cursor/rules/overseas-stock-tax.mdc` | clone the repo; Cursor loads the rule automatically |
| **Browser Web** | `index.html` | open with any static server / GitHub Pages |

---

## ⚠️ Important Disclaimer (Read First)

1. **The core purpose of this tool is to facilitate lawful tax compliance.** Paying tax in accordance with the law is a fundamental obligation of every Chinese citizen (Article 56, Constitution of the PRC).
2. **All output is for reference only.** The final filing amount must be determined by your competent tax authority.
3. This tool **does not** provide any illegal/grey tax-avoidance advice (hiding accounts, fabricating losses, deferring filing, etc.). If that is what you want, please do not use this tool.
4. **Strongly recommend** having a licensed tax practitioner / CPA review and sign off on the results before final filing, or directly consult the State Taxation Administration hotline 12366.
5. Authors of this project assume **no liability** for any tax, legal, or financial consequences from using this tool.

---

## 1. Why this project?

Starting in 2025, multiple municipal tax authorities in China (Beijing, Shanghai, Shenzhen, Xiamen, Sichuan, Shandong, Hubei, Zhejiang) have issued **back-tax notices** to Chinese residents trading HK/US stocks via overseas brokers (Futu, Longbridge, Tiger, etc.):

- Public cases range from RMB 120K to RMB 7M
- Trigger: **CRS (Common Reporting Standard) auto-exchange has landed.** The Hong Kong tax authority shares Futu/Longbridge account data with the State Taxation Administration of China.
- Core policy: Capital gains from overseas stocks fall under "Income from Property Transfer" — taxed at a **flat 20%**; **losses cannot be carried forward across years**; **dividends still incur tax even in loss years**.

But three pain points exist in practice:
1. Futu/Longbridge **do not produce China-tax-compliant cost basis reports**.
2. With hundreds/thousands of trades, dividends, splits, and transfers — **manual calculation is virtually impossible to get right**.
3. Weighted-average vs FIFO — different local tax bureaus accept different methods, and **a wrong choice can cost hundreds of thousands extra**.

This skill is built for that real-world scenario: **turn raw broker statements into tax-bureau-acceptable working papers + guide lawful compliance**.

---

## 2. Prerequisites: What you need to prepare

### 2.1 Download historical trading statements (most critical)

The skill cannot calculate without data. You must first download complete historical trading records from your broker(s).

#### Futu Securities / Moomoo (Hong Kong entity)

**Method A · Monthly statement PDFs** (most stable, all history)
1. Open Futu Niuniu APP (or desktop)
2. **Account → Account & Services → Reports → Monthly Statements**
3. Download month by month, covering all years you want to file
4. PDF password: may be last 6 of ID number / last 4 of phone + last 4 of ID / user-set (varies by region/era; the app will prompt the first time)

**Method B · Annual Statement Excel** (recommended, well-structured)
1. **Account → Assets → Historical Statements** → select year → export
2. Excel contains 11 sheets; the three sheets the skill consumes are: **Securities-Trading Flow / Securities-Cash Flow (dividends) / Securities-Asset Flow (transfers)**

**Method C · OpenAPI** (for technical users; bulk historical pulls)
- Docs: https://openapi.futunn.com/futu-api-doc/
- Personal accounts can apply; market data: HK LV2 + A-share LV1 free for mainland-IP users
- Endpoint: `get-history-order-fill-list`

> ⚠️ Futu has stopped onboarding new mainland-China users since June 2025. Existing users can still download all historical statements.

#### Longbridge (Longport)

**Monthly statement PDFs**
1. Longbridge APP or Web Trade
2. **My → Clearing Management → Statements**
3. Download month by month for all history
4. **PDF password = last 4 of phone + last 4 of ID number** (8 consecutive digits)

**Custom-range statements** (max 3 months per file)
- Same path → **Create temporary statement**

> 📝 Longbridge **does not provide** a China-tax-compliant cost basis report; the position P&L column cannot be used directly for tax filing.

#### Other brokers (Tiger, Xueying, IBKR, etc.)
This skill currently focuses on Futu and Longbridge. For other brokers, export to Excel and use the generic `parse_excel.py` fallback (column mapping may need manual adjustment).

### 2.2 Prepare RMB exchange rate history

Property-transfer income must be converted using the official central-parity rate. `assets/cny_mid_rate.json` includes year-end rates 2018-2024; for new years, manually update from:
- State Administration of Foreign Exchange: http://m.safe.gov.cn/safe/rmbhlzjj/
- China Foreign Exchange Trade System: https://www.chinamoney.org.cn/

### 2.3 Collect overseas tax-payment certificates (if you receive dividends)

- US-stock dividends → **Form 1042-S** (downloadable from broker)
- HK-stock dividends → Composite Statement / Dividend Voucher
- Required for foreign tax credit

### 2.4 Cross-broker stock transfers (if applicable)

If you transferred stocks between brokers (e.g., Futu ↔ Longbridge), you must obtain a **cost-basis transfer note** from the source broker. Without it, the skill cannot correctly continue the cost basis.

---

## 3. How it works

### 3.1 Architecture

```
User uploads broker statements (PDF/Excel)
    ↓
parse_futu.py / parse_longbridge.py  ← strict script-based parsing (NO vision recognition)
    ↓
Unified event stream (Trade + Dividend + Transfer + CrossBrokerTransfer + CorporateAction)
    ↓
cost_basis.py  ← 4 cost-basis algorithms run in parallel
    ↓
fx_rate.py     ← dual-track FX conversion (statutory / practical)
    ↓
compute_tax.py + penalty.py  ← tax due + late-payment surcharge
    ↓
reconcile.py   ← 5-node reconciliation gate
    ↓
render_report.py
    ↓
4 Excel outputs (working papers / B-form fields / algorithm comparison / summary report + compliance advice)
```

### 3.2 Four cost-basis algorithms

| Algorithm | Implementation | Legal basis / use case |
|---|---|---|
| **Rolling weighted average (default / gold standard)** | Each buy updates avg cost; each sell uses the pre-sell avg cost; remaining position avg unchanged | STA Announcement [2014] No. 67 (by analogy) + the "Yin Li version" already accepted by a local tax bureau |
| Moving weighted average | Equivalent to rolling weighted average at event granularity | Same as above |
| FIFO | Maintains a lot queue; sells consume from the head | CRS international convention; some local bureaus require it |
| Specific identification | User tags `lot_tag` at buy time; sell specifies which lot | Large positions / hedging strategies; cannot be constructed retroactively |

Tax base differences between algorithms can reach **30-50%**. The skill computes all four in parallel and outputs a comparison table.

### 3.3 Full corporate action support

| Event | Algorithm handling |
|---|---|
| **Split** 10:1 | qty × 10, unit cost ÷ 10, total cost unchanged |
| **Reverse split** 1:10 | qty ÷ 10, unit cost × 10 |
| **Bonus shares** 10-for-3 | qty +30%, total cost unchanged (cost dilution) |
| **Rights issue** 1:5 @ 8 | qty +20% + new lot at price 8 |
| **ADR ratio change** | Same as split |
| **Scrip dividend** | Treated as bonus; cash portion recorded separately as Dividend |

### 3.4 Transfer event support

| Type | Handling |
|---|---|
| **Same-broker internal transfer** (account upgrade / account migration) | Out releases cost to pending; In consumes pending; cost basis carried over, no taxable event |
| **Cross-broker transfer** (Futu → Longbridge etc.) | User must provide `CrossBrokerTransfer` with original cost basis; otherwise booked at zero cost with a warning |

### 3.5 Key policy interpretation

Based on: **MOF & STA Announcement [2020] No. 3**

- **Tax category**: Income from property transfer (capital gain) + interest/dividend income
- **Rate**: Both at flat **20%**
- **Offsetting**: Per Guoshuihan [2006] No. 1200, gains and losses **within the same year and same country** can offset; **losses cannot be carried forward across years**; **cross-country offsetting not allowed**
- **FX**: Statutory uses the year-end (Dec 31) central-parity rate; practical track locks original cost at the buy date's prior month-end rate (dual track output)
- **Filing window**: March 1 – June 30 of the following year, via Natural-Person e-Tax Bureau "Form B"
- **Late surcharge**: Each year independently from July 1 of the following year, daily rate 0.05% (annualized 18.25%), non-waivable

### 3.6 Four Excel outputs

| File | Content |
|---|---|
| `01_Detailed_Working_Paper_{year}.xlsx` | Full trades + 4-algorithm cost tracking + dual-FX (auditable by tax bureau) |
| `02_B_Form_Filing_Fields_{year}.xlsx` | Directly maps to "Annual Self-Assessment (Form B)" + "Foreign Tax Credit Detail Form" |
| `03_Four_Algorithm_Comparison_{year}.xlsx` | 4 algorithms × 2 FX tracks = 8 tax-base scenarios + selection guide |
| `04_Summary_Report_and_Compliance_Advice_{year}.xlsx` | Tax summary + 10 lawful tax-saving tips + 8 risk warnings + mandatory compliance disclaimer |

### 3.7 Five-node reconciliation gate

Any failed node blocks output:
1. **Data integrity** — parser output vs raw PDF count/amount
2. **Algorithm invariants** — final position must match across all 4 algorithms
3. **Period reconciliation** — sum(per-trade) == sum(monthly) == sum(quarterly) == sum(annual)
4. **Gold-standard regression** — Yin Li version 2021-02 monthly P&L must = RMB 15,000.00
5. **Final review** — Excel field alignment with B-form (independent agent)

---

## 4. Installation & Usage

### 4.1 As a Claude Code skill (recommended)

```bash
# 1. Clone into ~/.claude/skills/
cd ~/.claude/skills
git clone https://github.com/fourierwang66666/overseas-stock-tax.git

# 2. Install Python dependencies
pip install pdfplumber openpyxl pypdf

# 3. In Claude Code, just say:
# "Help me calculate my 2024 Futu HK/US stock tax"
# The skill auto-loads and walks you through the 8-step workflow.
```

### 4.2 Direct CLI usage

```bash
cd scripts/

# 1. Parse Futu monthly statement PDF → JSON
python3 parse_futu.py futu_statement_2024-12.pdf --password XXXX --out trades.json

# 2. Run 5-node reconciliation
python3 reconcile.py --trades trades.json

# 3. Generate 4 Excel outputs
python3 render_report.py --trades trades.json --year 2024 --out-dir ./output
```

---

## 5. Project layout

```
overseas-stock-tax/
├── README.md                    # Chinese README
├── README_EN.md                 # This file
├── SKILL.md                     # Claude Code skill entry
├── references/                  # 8 policy & algorithm docs (Chinese)
│   ├── 01-Policy basis.md
│   ├── 02-Calculation method & 4-algorithm comparison.md
│   ├── 03-Futu statement field mapping.md
│   ├── 04-Longbridge statement field mapping.md
│   ├── 05-Dual-track FX conversion.md
│   ├── 06-5-city tax bureau practices.md
│   ├── 07-Tax-bureau-accepted weighted-average method.md
│   └── 08-Late surcharge & foreign tax credit.md
├── scripts/
│   ├── cost_basis.py            # 4 cost algorithms + corporate actions + transfers
│   ├── parse_futu.py            # Futu parser (PDF / monthly / annual)
│   ├── parse_longbridge.py      # Longbridge parser
│   ├── fx_rate.py               # CNY central-parity FX
│   ├── compute_tax.py           # Tax calculation
│   ├── penalty.py               # Late-payment surcharge
│   ├── reconcile.py             # 5-node reconciliation
│   ├── render_report.py         # 4 Excel outputs
│   ├── schema.py                # Unified data schema
│   └── test_cost_basis.py       # 12 unit tests
├── templates/                   # Excel templates (embedded in render_report.py)
└── assets/
    └── cny_mid_rate.json        # Historical CNY central-parity rates
```

---

## 6. Compliance reaffirmed

- ✓ This project aims to **lower compliance friction and guide lawful tax filing**.
- ✓ All output is **for reference only**; final amounts determined by the competent tax authority.
- ✓ **Strongly recommend** having a licensed tax practitioner sign off.
- ✓ This project **rejects** any advice involving concealment, fabrication, or forgery.
- ✓ Project authors assume **no liability** for any tax / legal / financial consequences.

If this project lowers your bar to lawful tax compliance, please Star ⭐ / Issue / PR.

License: MIT
