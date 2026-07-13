// collect_models.mjs — 다나와 판매실적에서 전기차 모델별 판매를 수집 → master(dim=model) 갱신
// 사용법:
//   node scripts/collect_models.mjs            # 직전 완결 월
//   node scripts/collect_models.mjs 202606     # 특정 월
//   node scripts/collect_models.mjs 202601 202606   # 범위(포함)
// 소스: auto.danawa.com (모델 차원 전용). config/ev_models.json 화이트리스트(다나와 모델ID)로 EV만 필터.
import fs from "node:fs";
import path from "node:path";
import { ROOT, loadEvModels, fetchDanawaModels, upsertMonthDims } from "./lib.mjs";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function prevMonth() {
  const d = new Date();
  d.setDate(1); d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function monthRange(start, end) {
  const out = [];
  let y = +start.slice(0, 4), m = +start.slice(4, 6);
  const ey = +end.slice(0, 4), em = +end.slice(4, 6);
  while (y < ey || (y === ey && m <= em)) {
    out.push(`${y}${String(m).padStart(2, "0")}`);
    m++; if (m > 12) { m = 1; y++; }
  }
  return out;
}

async function collectMonth(ym, whitelist) {
  console.log(`\n=== ${ym} 다나와 모델 수집 ===`);
  const all = await fetchDanawaModels(ym); // {id: {name,count,nation}}
  const rows = [];
  const missing = [];
  for (const w of whitelist) {
    const hit = all[w.danawaId];
    const count = hit ? hit.count : 0;
    if (!hit) missing.push(w.name);
    rows.push({
      month: ym, dim: "model", code: w.danawaId,
      label: w.name, maker: w.maker ?? "", count,
    });
  }
  const sum = rows.reduce((s, r) => s + r.count, 0);
  console.log(`  화이트리스트 ${whitelist.length}개 중 ${whitelist.length - missing.length}개 매칭, 합계 ${sum.toLocaleString()}대`);
  if (missing.length) console.log(`  (이번 달 판매 0/미등장: ${missing.slice(0, 8).join(", ")}${missing.length > 8 ? " …" : ""})`);

  // raw 저장 (다나와 전체 스냅샷도 함께 — 나중에 화이트리스트 보강용)
  const rawDir = path.join(ROOT, "data", "raw");
  fs.mkdirSync(rawDir, { recursive: true });
  fs.writeFileSync(path.join(rawDir, `${ym}_danawa.json`),
    JSON.stringify({ ym, collectedAt: new Date().toISOString(), all }, null, 2), "utf8");

  upsertMonthDims(ym, ["model"], rows);
  console.log(`  master 갱신 완료 (${ym}, model ${rows.length}행)`);
}

async function main() {
  const whitelist = loadEvModels();
  if (!whitelist.length) { console.error("config/ev_models.json 이 비어 있습니다."); process.exit(1); }
  const a = process.argv[2], b = process.argv[3];
  const months = !a ? [prevMonth()] : b ? monthRange(a, b) : [a];
  console.log(`수집 대상 월: ${months.join(", ")}`);
  for (const ym of months) {
    try { await collectMonth(ym, whitelist); await sleep(400); }
    catch (e) { console.error(`[실패] ${ym}: ${e.message}`); process.exitCode = 1; }
  }
  console.log("\n완료. 대시보드 갱신: node scripts/build_dashboard.mjs");
}
main();
