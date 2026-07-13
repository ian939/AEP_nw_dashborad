// ev_master.csv → 대시보드용 DATA 객체 계산 (dark/SK 빌더 공용 SSOT)
import { readMaster } from "./lib.mjs";

// 대시보드가 쓰는 DATA 객체를 계산해 반환한다. (build_dashboard.mjs / build_sk_dashboard.mjs 공용)
export function buildDashboardData() {
  const master = readMaster();

  const months = [...new Set(master.map((r) => r.month))].sort();
  const pick = (dim) => master.filter((r) => r.dim === dim);

  // 월별 시리즈
  const seriesByCode = (dim, code) =>
    months.map((m) => {
      const row = master.find((r) => r.month === m && r.dim === dim && r.code === code);
      return row ? row.count : 0;
    });

  // 월별 모델 합계 (다나와). API 총계가 없을 때 EV 월별 총계의 폴백으로 사용.
  const modelSumByMonth = (m) =>
    master.filter((r) => r.month === m && r.dim === "model").reduce((s, r) => s + r.count, 0);

  const evTotalApi = seriesByCode("total", "EV");
  const allMonthly = seriesByCode("total", "ALL");
  // EV 월별: API 총계 우선, 없으면 다나와 모델 합계로 폴백
  const evMonthly = months.map((m, i) => evTotalApi[i] || modelSumByMonth(m));
  const evSource = months.map((m, i) => (evTotalApi[i] ? "api" : (modelSumByMonth(m) ? "danawa" : "none")));
  const shareMonthly = months.map((_, i) => allMonthly[i] ? +(evMonthly[i] / allMonthly[i] * 100).toFixed(1) : 0);
  const cumulative = evMonthly.reduce((acc, v) => { acc.push((acc.at(-1) || 0) + v); return acc; }, []);
  const hasFuel = master.some((r) => r.dim === "fuel");
  const hasRegion = master.some((r) => r.dim === "region");
  const hasApiTotal = evTotalApi.some((v) => v > 0);

  const latest = months.at(-1);
  const prev = months.at(-2);
  const evLatest = evMonthly.at(-1) || 0;
  const evPrev = prev ? (evMonthly.at(-2) || 0) : null;
  const mom = evPrev ? +(((evLatest - evPrev) / evPrev) * 100).toFixed(1) : null;
  const ytd = months.reduce((s, m, i) =>
    s + (latest && m.slice(0, 4) === latest.slice(0, 4) ? evMonthly[i] : 0), 0);

  // 최신월 연료 믹스 (전기 vs 나머지 합)
  const fuelLatest = pick("fuel").filter((r) => r.month === latest);
  const evFuel = fuelLatest.find((r) => r.code === "5")?.count || 0;
  const otherFuel = fuelLatest.filter((r) => r.code !== "5").reduce((s, r) => s + r.count, 0);

  // 최신월 지역별
  const regionLatest = pick("region").filter((r) => r.month === latest)
    .sort((a, b) => b.count - a.count);

  // 모델 dim을 label(모델명)로 집계 — 페이스리프트로 다나와 ID가 갈린 동일 모델을 합산
  const modelRows = master.filter((r) => r.dim === "model");
  const modelLabels = [...new Set(modelRows.map((r) => r.label))];
  const modelSeriesByLabel = {};
  for (const lab of modelLabels) {
    modelSeriesByLabel[lab] = months.map((m) =>
      modelRows.filter((r) => r.month === m && r.label === lab).reduce((s, r) => s + r.count, 0));
  }
  const lastIdx = months.length - 1;
  const modelLatest = modelLabels
    .map((lab) => ({ label: lab, count: modelSeriesByLabel[lab][lastIdx] || 0 }))
    .sort((a, b) => b.count - a.count);
  const topModelTrend = modelLatest.slice(0, 15).map((r) => ({
    label: r.label,
    data: modelSeriesByLabel[r.label],
  }));

  return {
    months, evMonthly, allMonthly, shareMonthly, cumulative, evSource,
    hasFuel, hasRegion, hasApiTotal,
    kpi: { latest, evLatest, mom, ytd, cumulative: cumulative.at(-1) || 0 },
    fuel: { ev: evFuel, other: otherFuel },
    region: { labels: regionLatest.map((r) => r.label), data: regionLatest.map((r) => r.count) },
    modelTop: {
      labels: modelLatest.slice(0, 15).map((r) => r.label),
      data: modelLatest.slice(0, 15).map((r) => r.count),
    },
    modelTrend: topModelTrend,
    generatedAt: new Date().toISOString(),
  };
}
