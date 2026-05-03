// app.js — 浏览器入口
// 流程：加载 Pyodide → 装 openpyxl → 写入 skill 脚本 → 用户上传 → 运行 → 下载

const $ = (s) => document.querySelector(s);
const log = (msg) => {
  const el = $('#log');
  el.textContent += msg + '\n';
  el.scrollTop = el.scrollHeight;
};
const setStatus = (s) => { $('#status').textContent = s; };

let pyodide = null;
let pyReady = false;
let uploadedFiles = [];

// ===== 1. 加载 Pyodide + 依赖 =====
async function bootPyodide() {
  setStatus('正在加载 Pyodide 运行时...');
  pyodide = await loadPyodide({
    indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.27.2/full/'
  });
  setStatus('正在安装 Python 包（micropip + openpyxl）...');
  // openpyxl 不是 Pyodide 内置包，需通过 micropip 从 PyPI 安装
  await pyodide.loadPackage(['micropip']);
  await pyodide.runPythonAsync(`
import micropip
await micropip.install('openpyxl')
`);
  setStatus('正在加载计算引擎...');
  await loadSkillScripts();
  pyReady = true;
  setStatus('✅ 引擎就绪。请勾选声明并选择文件。');
  $('#runBtn').disabled = !canRun();
}

async function loadSkillScripts() {
  // 把 skill 的 Python 脚本嵌入到 pyodide 文件系统
  const files = [
    'cost_basis.py', 'schema.py', 'fx_rate.py',
    'compute_tax.py', 'penalty.py', 'reconcile.py',
    'render_report.py', 'parse_excel_browser.py'
  ];
  pyodide.FS.mkdirTree('/skill/scripts');
  pyodide.FS.mkdirTree('/skill/assets');
  for (const f of files) {
    const resp = await fetch(`./scripts/${f}`);
    if (!resp.ok) throw new Error(`无法加载 ${f}`);
    const text = await resp.text();
    pyodide.FS.writeFile(`/skill/scripts/${f}`, text);
  }
  // 汇率数据（与 skill 后端共享 assets/）
  const fx = await fetch('./assets/cny_mid_rate.json');
  pyodide.FS.writeFile('/skill/assets/cny_mid_rate.json', await fx.text());
  pyodide.runPython(`
import sys
sys.path.insert(0, '/skill/scripts')
`);
}

// ===== 2. 模式切换 =====
document.querySelectorAll('input[name="mode"]').forEach(r =>
  r.addEventListener('change', e => {
    $('#apiKeySection').classList.toggle('hidden', e.target.value !== 'ai');
  })
);

// 持久化 API key
const savedKey = localStorage.getItem('anthropic_api_key');
if (savedKey) $('#apiKey').value = savedKey;
$('#apiKey').addEventListener('change', e => {
  if (e.target.value) localStorage.setItem('anthropic_api_key', e.target.value);
  else localStorage.removeItem('anthropic_api_key');
});

// ===== 3. 文件上传 =====
const dz = $('#dropzone');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
dz.addEventListener('drop', e => {
  e.preventDefault();
  dz.classList.remove('drag');
  handleFiles(e.dataTransfer.files);
});
$('#fileInput').addEventListener('change', e => handleFiles(e.target.files));

function handleFiles(files) {
  uploadedFiles = Array.from(files);
  $('#fileList').innerHTML = uploadedFiles
    .map(f => `<div class="file-item">📄 ${f.name} <small>(${(f.size/1024).toFixed(1)} KB)</small></div>`)
    .join('');
  $('#runBtn').disabled = !canRun();
}

// ===== 4. 声明勾选 =====
$('#acceptDisclaimer').addEventListener('change', () => {
  $('#runBtn').disabled = !canRun();
});

function canRun() {
  return pyReady
    && $('#acceptDisclaimer').checked
    && uploadedFiles.length > 0;
}

// ===== 5. 运行计算 =====
$('#runBtn').addEventListener('click', runCalculation);

async function runCalculation() {
  $('#runBtn').disabled = true;
  $('#log').textContent = '';
  $('#resultSection').classList.add('hidden');
  setStatus('正在解析交易明细...');

  try {
    // 5.1 把所有上传文件写入 pyodide 文件系统
    pyodide.FS.mkdirTree('/uploads');
    for (const file of uploadedFiles) {
      const buf = new Uint8Array(await file.arrayBuffer());
      pyodide.FS.writeFile(`/uploads/${file.name}`, buf);
    }

    const year = parseInt($('#settlementYear').value);
    const method = $('#defaultMethod').value;
    const mode = document.querySelector('input[name="mode"]:checked').value;

    // 5.2 解析所有文件 → 统一 trades JSON
    log(`[${new Date().toLocaleTimeString()}] 解析 ${uploadedFiles.length} 个文件...`);
    const parseCode = `
import os, json
from parse_excel_browser import parse_any_excel

trades = []
errors = []
for fn in os.listdir('/uploads'):
    path = '/uploads/' + fn
    try:
        ts = parse_any_excel(path)
        trades.extend(ts)
        print(f"  ✓ {fn}: {len(ts)} 笔")
    except Exception as e:
        errors.append(f"{fn}: {e}")
        print(f"  ✗ {fn}: {e}")

import json
result = json.dumps({'trades_count': len(trades), 'errors': errors})
result
`;
    const parseResult = JSON.parse(pyodide.runPython(parseCode));
    log(pyodide.runPython('import io,sys; sys.stdout.flush(); ""'));
    log(`解析完成：${parseResult.trades_count} 笔交易`);
    if (parseResult.errors.length) {
      log('⚠️ 部分文件解析失败：');
      parseResult.errors.forEach(e => log('  ' + e));
    }

    if (parseResult.trades_count === 0) {
      throw new Error('未解析出任何交易记录。请检查上传文件是否为富途/长桥的 Excel 月结单或年度账单。');
    }

    // 5.3 跑 4 节点对账
    setStatus('正在跑 4 节点对账...');
    log(`[${new Date().toLocaleTimeString()}] 5 节点对账...`);
    const reconCode = `
from reconcile import (
    reconcile_algorithm_invariants,
    reconcile_period_aggregation,
    reconcile_baseline_regression,
)
import json

# trades 已在内存 (上一段 parse 输出)
report = {
    'node2': reconcile_algorithm_invariants(trades),
    'node3': reconcile_period_aggregation(trades, '${method}'),
    'node4': reconcile_baseline_regression(),
}
json.dumps(report, default=str)
`;
    const reconReport = JSON.parse(pyodide.runPython(reconCode));
    for (const [k, v] of Object.entries(reconReport)) {
      log(`  ${k}: ${v.pass ? '✅ pass' : '❌ FAIL'}`);
    }
    if (!reconReport.node2.pass || !reconReport.node3.pass || !reconReport.node4.pass) {
      throw new Error('对账失败，已阻塞输出。请联系开发者。');
    }

    // 5.4 AI 模式：调 Claude API 增强（节税建议）
    let aiAdvice = '';
    if (mode === 'ai') {
      const apiKey = $('#apiKey').value.trim();
      if (!apiKey) throw new Error('AI 模式需要填入 Anthropic API Key。');
      setStatus('正在调用 Claude API 生成节税建议...');
      log(`[${new Date().toLocaleTimeString()}] 调用 Claude API（仅生成合法节税建议，不发送任何交易明细）...`);
      aiAdvice = await callClaudeForAdvice(apiKey, year, $('#city').value);
      log('  AI 建议已收到');
    }

    // 5.5 生成 4 份 Excel
    setStatus('正在生成 4 份 Excel...');
    log(`[${new Date().toLocaleTimeString()}] 渲染 Excel...`);
    const renderCode = `
from render_report import render_ledger, render_b_form, render_method_compare, render_summary_report
from pathlib import Path
out = Path('/output')
out.mkdir(exist_ok=True)
y = ${year}
render_ledger(trades, y, out / f'01_逐笔交易底稿_{y}.xlsx')
render_b_form(trades, y, out / f'02_B表填报字段_{y}.xlsx')
render_method_compare(trades, y, out / f'03_四算法对比表_{y}.xlsx')
render_summary_report(trades, y, out / f'04_税务综合报告与合规建议_{y}.xlsx')
import os
files = sorted(os.listdir('/output'))
import json
json.dumps(files)
`;
    const fileNames = JSON.parse(pyodide.runPython(renderCode));
    log(`  生成 ${fileNames.length} 个文件`);

    // 5.6 计算汇总数字（展示在结果卡）
    const sumCode = `
from cost_basis import weighted_avg
from compute_tax import compute_capital_gains
import json
events = weighted_avg(trades)
res = compute_capital_gains(events, settlement_year=${year}, fx_track='A')
json.dumps({
    'total_tax': str(res['total_tax']),
    'total_pnl': str(res['total_gross_pnl']),
    'pools': len(res['by_pool']),
    'sells': sum(p['sell_count'] for p in res['by_pool'].values()),
}, default=str)
`;
    const summary = JSON.parse(pyodide.runPython(sumCode));

    renderResult(summary, fileNames, aiAdvice);
    setStatus('✅ 计算完成');
    log(`[${new Date().toLocaleTimeString()}] ✅ 全部完成`);

  } catch (err) {
    log('❌ 错误: ' + err.message);
    setStatus('❌ 计算失败');
    console.error(err);
  } finally {
    $('#runBtn').disabled = !canRun();
  }
}

function renderResult(summary, fileNames, aiAdvice) {
  $('#resultSection').classList.remove('hidden');

  $('#summary').innerHTML = `
    <h3>📊 计算摘要（默认：滚动加权平均 + 法规口径年末汇率）</h3>
    <table>
      <tr><th>纳税年度</th><td class="num">${$('#settlementYear').value}</td></tr>
      <tr><th>卖出笔数</th><td class="num">${summary.sells}</td></tr>
      <tr><th>盈亏池数（按年×国别）</th><td class="num">${summary.pools}</td></tr>
      <tr><th>应纳税所得额合计 (CNY)</th><td class="num">${parseFloat(summary.total_pnl).toFixed(2)}</td></tr>
      <tr><th><strong>应纳税额合计 (CNY) @20%</strong></th><td class="num"><strong>${parseFloat(summary.total_tax).toFixed(2)}</strong></td></tr>
    </table>
    ${aiAdvice ? `<details style="margin-top:12px"><summary>🤖 AI 节税建议（仅合法范围）</summary><pre style="white-space:pre-wrap;font-family:inherit;font-size:13px;color:#1f2937">${aiAdvice}</pre></details>` : ''}
  `;

  // 下载链接
  const dl = $('#downloads');
  dl.innerHTML = '';
  for (const fn of fileNames) {
    const data = pyodide.FS.readFile(`/output/${fn}`);
    const blob = new Blob([data], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fn;
    a.textContent = '⬇️ ' + fn;
    dl.appendChild(a);
  }
}

// ===== 6. AI 增强模式：调 Claude API =====
async function callClaudeForAdvice(apiKey, year, city) {
  // 注意：只发送年度+城市等元数据，不发送任何交易明细
  const prompt = `你是中国税务师，为${city}居民${year}纳税年度的海外股票个税申报提供合法节税建议。

要求：
1. 仅限中国税法允许的合法范围
2. 严禁建议任何隐匿账户、虚构亏损、推迟申报等违法行为
3. 5 条以内，每条 1-2 句话
4. 末尾强调：本建议仅供参考，最终以主管税务机关认定为准；建议委托执业税务师签字`;

  const resp = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'anthropic-dangerous-direct-browser-access': 'true',
    },
    body: JSON.stringify({
      model: 'claude-sonnet-4-5',
      max_tokens: 1024,
      messages: [{ role: 'user', content: prompt }],
    }),
  });
  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Claude API 调用失败：${resp.status} ${err}`);
  }
  const data = await resp.json();
  return data.content[0].text;
}

// 启动
bootPyodide().catch(e => {
  setStatus('❌ Pyodide 加载失败: ' + e.message);
  console.error(e);
});
