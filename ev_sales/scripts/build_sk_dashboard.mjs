// build_sk_dashboard.mjs — master CSV → repo/의 SK electlink 톤 대시보드(EV Sales Dashboard.html) 생성
import fs from "node:fs";
import path from "node:path";
import { ROOT } from "./lib.mjs";
import { buildDashboardData } from "./dashboard_data.mjs";

const DATA = buildDashboardData();
const months = DATA.months;
const latest = DATA.kpi.latest;
if (months.length === 0) {
  console.warn("[경고] master 데이터가 비어 있습니다. collect_models.mjs 로 수집 후 다시 실행하세요.");
}

// 출력 위치: index.html 이 있는 repo 루트를 찾는다.
// - 로컬 EV 트래커: ROOT 형제의 repo/ 폴더
// - repo/ev_sales/ 로 vendor된 CI 복사본: ROOT 바로 위(repo 루트)
// 두 위치에서 동일 파일로 동작하도록 위로 탐색 후 형제 repo/ 폴백.
function findRepoRoot() {
  let d = ROOT;
  for (let i = 0; i < 4; i++) {
    if (fs.existsSync(path.join(d, "index.html"))) return d;
    d = path.dirname(d);
  }
  const sib = path.resolve(ROOT, "..", "repo");
  if (fs.existsSync(path.join(sib, "index.html"))) return sib;
  return null;
}
const OUT_DIR = findRepoRoot();
if (!OUT_DIR) {
  console.error("[오류] index.html 이 있는 repo 루트를 찾지 못했습니다.");
  process.exit(1);
}
const OUT = path.join(OUT_DIR, "EV Sales Dashboard.html");

const html = `<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>SK electlink | Domestic EV Sales Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --navy: #1a2b5c;
    --navy-dark: #0f1a3d;
    --navy-light: #2d4380;
    --sk-red: #e4002b;
    --sk-orange: #ff7a00;
    --ev: #0a6e73;
    --bg: #f5f6f8;
    --card: #ffffff;
    --border: #e2e5eb;
    --text: #1a1f36;
    --text-muted: #6b7280;
    --text-light: #9ca3af;
    --success: #059669;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); font-size: 13px; line-height: 1.5;
  }
  .header {
    background: var(--card); border-bottom: 1px solid var(--border);
    padding: 16px 32px; display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 100; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  }
  .brand { display: flex; align-items: center; gap: 14px; }
  .logo { display: flex; align-items: center; gap: 8px; }
  .logo-mark {
    width: 36px; height: 36px; background: var(--sk-red); border-radius: 6px;
    display: grid; place-items: center; color: white; font-weight: 900; font-size: 14px;
    letter-spacing: -0.5px; transform: rotate(-3deg);
  }
  .brand-text h1 { font-size: 15px; font-weight: 800; letter-spacing: -0.3px; }
  .brand-text p { font-size: 11px; color: var(--text-muted); margin-top: 1px; }
  .header-meta { display: flex; align-items: center; gap: 10px; font-size: 11px; color: var(--text-muted); }
  .header-meta a.home-link {
    color: var(--text-muted); text-decoration: none; padding: 6px 12px;
    border-radius: 6px; font-weight: 600; transition: all 0.15s;
  }
  .header-meta a.home-link:hover { background: var(--bg); color: var(--navy); }
  .header-meta .date { padding: 6px 12px; background: var(--bg); border-radius: 6px; font-weight: 600; color: var(--text); }

  .page-title { padding: 24px 32px 12px; max-width: 1600px; margin: 0 auto; width: 100%; }
  .page-title .eyebrow {
    font-size: 11px; font-weight: 700; letter-spacing: 1px;
    color: var(--sk-red); text-transform: uppercase; margin-bottom: 6px;
  }
  .page-title h2 { font-size: 24px; font-weight: 800; letter-spacing: -0.5px; }
  .page-title p { font-size: 13px; color: var(--text-muted); margin-top: 4px; }

  main { padding: 20px 32px 40px; max-width: 1600px; margin: 0 auto; width: 100%; }

  .grid-kpi { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }
  .kpi {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px; position: relative; overflow: hidden;
  }
  .kpi::after {
    content: ''; position: absolute; top: 0; right: 0; width: 40px; height: 40px;
    background: var(--sk-red); opacity: 0.06; border-radius: 0 0 0 40px;
  }
  .kpi .label {
    font-size: 10.5px; color: var(--text-muted); font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;
  }
  .kpi .value { font-size: 24px; font-weight: 800; color: var(--navy); letter-spacing: -0.8px; font-feature-settings: 'tnum'; }
  .kpi .value.txt { font-size: 19px; }
  .kpi .unit { font-size: 12px; color: var(--text-muted); font-weight: 600; margin-left: 3px; }
  .kpi .delta { margin-top: 6px; font-size: 11px; font-weight: 600; color: var(--text-muted); }
  .kpi .delta.up { color: var(--success); }
  .kpi .delta.down { color: var(--sk-red); }

  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin-bottom: 20px; }
  .grid-2 .card { margin-bottom: 0; }
  .card-header {
    padding: 14px 18px; border-bottom: 1px solid var(--border);
    display: flex; justify-content: space-between; align-items: center;
    background: linear-gradient(to bottom, #fafbfc, #fff); gap: 10px; flex-wrap: wrap;
  }
  .card-header h3 { font-size: 13px; font-weight: 700; letter-spacing: -0.2px; }
  .card-header .tag {
    font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 4px;
    background: var(--navy); color: white; letter-spacing: 0.3px; white-space: nowrap;
  }
  .card-header .tag.ev { background: var(--ev); }
  .card-header .tag.sales { background: var(--sk-red); }
  .card-body { padding: 18px; }
  .btns { display: flex; gap: 6px; }
  .btns button {
    font-size: 11px; padding: 4px 10px; border: 1px solid var(--border);
    background: #fff; color: var(--text-muted); border-radius: 6px; cursor: pointer; font-weight: 600;
  }
  .btns button:hover { color: var(--navy); border-color: var(--navy); }

  .chart-wrap { position: relative; height: 300px; }
  .chart-wrap.tall { height: 440px; }
  .ph {
    color: var(--text-light); text-align: center; padding: 40px 20px;
    font-size: 12.5px; line-height: 1.7; display: flex; align-items: center; justify-content: center; height: 100%;
  }
  .ph .badge {
    display: inline-block; margin-bottom: 8px; padding: 3px 10px; border-radius: 4px;
    background: #fff3e0; border: 1px dashed var(--sk-orange); color: var(--sk-orange); font-weight: 700; font-size: 10.5px;
  }
  .source {
    font-size: 10.5px; color: var(--text-light); font-style: italic;
    padding: 12px 18px; border-top: 1px solid var(--border); background: #fafbfc;
  }
  footer { text-align: center; padding: 24px 32px 40px; font-size: 11px; color: var(--text-light); }

  @media (max-width: 1100px) { .grid-2 { grid-template-columns: 1fr; } .grid-kpi { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 640px) {
    .header { padding: 12px 18px; } .page-title { padding: 20px 18px 8px; } main { padding: 16px 18px 40px; }
    .grid-kpi { grid-template-columns: 1fr 1fr; }
  }
</style>
</head>
<body>

<header class="header">
  <div class="brand">
    <div class="logo">
      <div class="logo-mark">SK</div>
      <div class="brand-text">
        <h1>SK electlink</h1>
        <p>Domestic EV Sales Dashboard</p>
      </div>
    </div>
  </div>
  <div class="header-meta">
    <a href="./index.html" class="home-link">← 홈</a>
    <span>성장지원팀</span>
    <span class="date" id="dataUpdatedBadge">—</span>
  </div>
</header>

<div class="page-title">
  <div class="eyebrow">Market Dashboard</div>
  <h2>국내 전기차 판매 추이</h2>
  <p id="pageSubtitle">Loading...</p>
</div>

<main id="app"></main>

<footer>© SK electlink · Internal use only · 모델별 판매 데이터: 다나와 판매실적(auto.danawa.com)</footer>

<script>
const DATA = ${JSON.stringify(DATA)};
const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('ko-KR'));
const ymLabel = (ym) => ym ? ym.slice(0, 4) + '.' + ym.slice(4, 6) : '—';
const app = document.getElementById('app');

// 헤더 배지 (생성 시각)
document.getElementById('dataUpdatedBadge').textContent =
  DATA.generatedAt ? 'Updated ' + DATA.generatedAt.slice(0, 10) : '—';

// 서브타이틀 (소스 + 기간)
document.getElementById('pageSubtitle').textContent =
  '모델별 판매(다나와 판매실적)' +
  (DATA.hasApiTotal ? ' · 총계·연료·지역: 공공데이터포털 KOTSA 신규등록 API' : ' · 연료·지역·공식총계는 KOTSA API 수집 후 표시') +
  (DATA.months.length ? ' · ' + ymLabel(DATA.months[0]) + ' ~ ' + ymLabel(DATA.months.at(-1)) : '');

if (!DATA.months.length) {
  app.innerHTML = '<div class="card"><div class="card-body"><div class="ph">데이터가 없습니다. collect_models.mjs 로 수집 후 build_sk_dashboard.mjs 를 다시 실행하세요.</div></div></div>';
} else {
  const mom = DATA.kpi.mom;
  const momHtml = mom == null
    ? '<div class="delta">—</div>'
    : '<div class="delta ' + (mom >= 0 ? 'up' : 'down') + '">' + (mom >= 0 ? '▲' : '▼') + ' ' + Math.abs(mom) + '% MoM</div>';
  const topModel = DATA.modelTop.labels[0] || '—';
  const topModelVal = DATA.modelTop.data[0] || 0;
  const phFuel = '<div class="ph"><div><span class="badge">API 대기</span><br>최신월 연료 비중은<br>KOTSA API 수집 후 표시됩니다.</div></div>';
  const phRegion = '<div class="ph"><div><span class="badge">API 대기</span><br>지역별 분포는<br>KOTSA API 수집 후 표시됩니다.</div></div>';
  const phShare = '<div class="ph"><div><span class="badge">API 대기</span><br>전기차 비중(%)은<br>KOTSA API 수집 후 표시됩니다.</div></div>';

  app.innerHTML = \`
    <div class="grid-kpi">
      <div class="kpi"><div class="label">Latest Month Sales (\${ymLabel(DATA.kpi.latest)})</div><div class="value">\${fmt(DATA.kpi.evLatest)}<span class="unit">대</span></div>\${momHtml}</div>
      <div class="kpi"><div class="label">YTD Sales (\${DATA.kpi.latest ? DATA.kpi.latest.slice(0,4) : '—'})</div><div class="value">\${fmt(DATA.kpi.ytd)}<span class="unit">대</span></div><div class="delta">올해 누계</div></div>
      <div class="kpi"><div class="label">Cumulative (Collected)</div><div class="value">\${fmt(DATA.kpi.cumulative)}<span class="unit">대</span></div><div class="delta">\${ymLabel(DATA.months[0])} ~</div></div>
      <div class="kpi"><div class="label">#1 Model (\${ymLabel(DATA.kpi.latest)})</div><div class="value txt">\${topModel}</div><div class="delta">\${fmt(topModelVal)}대</div></div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><h3># 월별 전기차 판매</h3><span class="tag sales">Sales</span></div>
        <div class="card-body"><div class="chart-wrap"><canvas id="cEv"></canvas></div></div>
      </div>
      <div class="card">
        <div class="card-header"><h3>최신월 연료 비중 (전기 vs 기타)</h3><span class="tag ev">EV</span></div>
        <div class="card-body"><div class="chart-wrap">\${DATA.hasFuel ? '<canvas id="cFuel"></canvas>' : phFuel}</div></div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><h3>모델별 판매 TOP 15 (\${ymLabel(DATA.kpi.latest)})</h3><span class="tag">Model</span></div>
        <div class="card-body"><div class="chart-wrap tall"><canvas id="cModel"></canvas></div></div>
      </div>
      <div class="card">
        <div class="card-header"><h3>지역별 분포 (\${ymLabel(DATA.kpi.latest)})</h3><span class="tag ev">EV</span></div>
        <div class="card-body"><div class="chart-wrap tall">\${DATA.hasRegion ? '<canvas id="cRegion"></canvas>' : phRegion}</div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><h3>모델별 월별 판매 추이 (TOP 15)</h3><div class="btns"><button id="trAll">전체 선택</button><button id="trNone">전체 해제</button></div></div>
      <div class="card-body"><div class="chart-wrap tall"><canvas id="cTrend"></canvas></div></div>
      <div class="source">범례를 클릭해 개별 모델을 켜고 끌 수 있습니다. 다나와 모델ID 기준, 페이스리프트 세대는 동일 모델명으로 합산.</div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><h3>전기차 비중(%) 추이</h3><span class="tag ev">EV</span></div>
        <div class="card-body"><div class="chart-wrap">\${DATA.hasApiTotal ? '<canvas id="cShare"></canvas>' : phShare}</div></div>
      </div>
      <div class="card">
        <div class="card-header"><h3>누적 등록 대수 (수집기간)</h3><span class="tag sales">Sales</span></div>
        <div class="card-body"><div class="chart-wrap"><canvas id="cCum"></canvas></div></div>
      </div>
    </div>\`;

  // ===== Chart.js =====
  const NAVY = '#1a2b5c', RED = '#e4002b', ORANGE = '#ff7a00', EV = '#0a6e73', BORDER = '#e2e5eb', MUTED = '#6b7280';
  const PALETTE = ['#1a2b5c','#0a6e73','#e4002b','#ff7a00','#2d4380','#8a5fbf','#059669','#b0862b','#c0392b','#0e8a8a','#5f7a8a','#a63a6b','#4c7a2b','#2b7ab0','#d9542b'];
  Chart.defaults.color = MUTED;
  Chart.defaults.borderColor = BORDER;
  Chart.defaults.font.family = "'Pretendard',-apple-system,'Segoe UI',sans-serif";
  const noLegend = { plugins: { legend: { display: false } } };
  const base = { responsive: true, maintainAspectRatio: false, animation: false };
  const mLabels = DATA.months.map(ymLabel);

  // 최신월 전기차 총계 (모델별 비중 분모)
  const evTotalLatest = DATA.kpi.evLatest || DATA.modelTop.data.reduce((a, b) => a + b, 0);
  const pct = (v, t) => t ? (v / t * 100).toFixed(1) : '0.0';

  // TOP15 막대 끝에 비중(%) 라벨을 그리는 커스텀 플러그인
  const modelPctPlugin = {
    id: 'modelPct',
    afterDatasetsDraw(chart) {
      const { ctx } = chart; const meta = chart.getDatasetMeta(0);
      ctx.save();
      ctx.font = "600 11px 'Pretendard',sans-serif";
      ctx.fillStyle = MUTED; ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
      meta.data.forEach((el, i) => {
        const v = chart.data.datasets[0].data[i]; if (!v) return;
        ctx.fillText(pct(v, evTotalLatest) + '%', el.x + 6, el.y);
      });
      ctx.restore();
    }
  };

  new Chart(cEv, { type: 'bar', data: { labels: mLabels, datasets: [{ label: '전기차 판매', data: DATA.evMonthly, backgroundColor: NAVY, borderRadius: 4 }] },
    options: { ...base, ...noLegend, plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ' ' + c.raw.toLocaleString() + '대' } } } } });

  if (DATA.hasFuel) new Chart(cFuel, { type: 'doughnut', data: { labels: ['전기', '기타 연료'], datasets: [{ data: [DATA.fuel.ev, DATA.fuel.other], backgroundColor: [EV, BORDER], borderWidth: 0 }] },
    options: { ...base, cutout: '62%' } });

  new Chart(cModel, { type: 'bar', data: { labels: DATA.modelTop.labels, datasets: [{ data: DATA.modelTop.data, backgroundColor: NAVY, borderRadius: 4 }] },
    options: { ...base, indexAxis: 'y', scales: { x: { grace: '14%' } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => ' ' + c.raw.toLocaleString() + '대 (' + pct(c.raw, evTotalLatest) + '%)' } } } },
    plugins: [modelPctPlugin] });

  if (DATA.hasRegion) new Chart(cRegion, { type: 'bar', data: { labels: DATA.region.labels, datasets: [{ data: DATA.region.data, backgroundColor: EV, borderRadius: 4 }] },
    options: { ...base, ...noLegend } });

  const trendColors = DATA.modelTrend.map((s, i) => PALETTE[i % PALETTE.length]);
  const trendChart = new Chart(cTrend, { type: 'line',
    data: { labels: mLabels, datasets: DATA.modelTrend.map((s, i) => ({ label: s.label, data: s.data, borderColor: trendColors[i], backgroundColor: 'transparent', tension: .35, borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 5 })) },
    options: { ...base, interaction: { mode: 'nearest', intersect: false, axis: 'x' },
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, padding: 8 } },
        tooltip: { callbacks: { label: (c) => ' ' + c.dataset.label + ': ' + c.raw.toLocaleString() + '대 (' + pct(c.raw, DATA.evMonthly[c.dataIndex] || 0) + '%)' } } } } });

  const setAllTrend = (vis) => { trendChart.data.datasets.forEach((_, i) => trendChart.setDatasetVisibility(i, vis)); trendChart.update(); };
  document.getElementById('trAll').onclick = () => setAllTrend(true);
  document.getElementById('trNone').onclick = () => setAllTrend(false);

  if (DATA.hasApiTotal) new Chart(cShare, { type: 'line', data: { labels: mLabels, datasets: [{ data: DATA.shareMonthly, borderColor: EV, backgroundColor: 'transparent', tension: .3, borderWidth: 2, pointRadius: 2 }] },
    options: { ...base, ...noLegend, scales: { y: { ticks: { callback: v => v + '%' } } } } });

  new Chart(cCum, { type: 'line', data: { labels: mLabels, datasets: [{ data: DATA.cumulative, borderColor: NAVY, backgroundColor: 'rgba(26,43,92,.12)', fill: true, tension: .3, borderWidth: 2, pointRadius: 0 }] },
    options: { ...base, ...noLegend } });
}
</script>
</body>
</html>`;

fs.writeFileSync(OUT, html, "utf8");
console.log(`EV Sales Dashboard.html 생성 완료 → ${OUT} (${months.length}개월, 최신 ${latest || "-"})`);
