// lib.mjs — 공용 상수 및 헬퍼 (수집/대시보드 공용)
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(__dirname, "..");

export const FUELS = {
  1: "CNG", 2: "경유", 3: "수소", 4: "LPG", 5: "전기",
  6: "하이브리드(CNG+전기)", 7: "하이브리드(휘발유+전기)",
  8: "휘발유", 9: "휘발유(무연)", 10: "휘발유(유연)", 11: "기타연료",
};
export const EV_FUEL_CODE = 5;

export const REGIONS = {
  1: "서울", 2: "부산", 3: "대구", 4: "인천", 5: "광주", 6: "대전",
  7: "울산", 8: "세종", 9: "경기", 10: "강원", 11: "충북", 12: "충남",
  13: "전북", 14: "전남", 15: "경북", 16: "경남", 17: "제주",
};

export function loadSecret() {
  const p = path.join(ROOT, "config", "secret.json");
  if (!fs.existsSync(p)) throw new Error("config/secret.json 이 없습니다.");
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

export function loadEvModels() {
  const p = path.join(ROOT, "config", "ev_models.json");
  if (!fs.existsSync(p)) return [];
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// XML 또는 JSON 응답에서 resultCode / dtaCo 추출
function parseCount(text) {
  const t = text.trim();
  // JSON 시도
  if (t.startsWith("{")) {
    try {
      const j = JSON.parse(t);
      const h = j?.response?.header ?? {};
      const b = j?.response?.body ?? {};
      const code = String(h.resultCode ?? "").padStart(2, "0");
      const dta = b.dtaCo ?? b.dtaco ?? b.totalCount;
      return { code, msg: h.resultMsg ?? "", count: dta == null ? null : Number(dta) };
    } catch { /* XML으로 폴백 */ }
  }
  const grab = (tag) => {
    const m = t.match(new RegExp(`<${tag}>\\s*([\\s\\S]*?)\\s*</${tag}>`, "i"));
    return m ? m[1].trim() : null;
  };
  const code = grab("resultCode");
  const msg = grab("resultMsg");
  const dta = grab("dtaCo");
  return {
    code: code == null ? null : code,
    msg: msg ?? "",
    count: dta == null ? null : Number(dta),
  };
}

/**
 * 신규등록 통계 카운트 1건 조회. 재시도/백오프 포함.
 * @param {object} cfg {service_key, endpoint, opPath?}
 * @param {object} params registYy/registMt/useFuelCode/registGrcCode/cnmCode ...
 * @returns {Promise<number>} 건수 (NODATA는 0)
 */
export async function fetchCount(cfg, params, { retries = 4, baseDelay = 800 } = {}) {
  const opPath = cfg.opPath ? "/" + cfg.opPath.replace(/^\//, "") : "";
  const url = new URL(cfg.endpoint + opPath);
  url.searchParams.set("serviceKey", cfg.service_key);
  url.searchParams.set("type", "xml");
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }

  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(20000) });
      const text = await res.text();
      if (res.status >= 500) { lastErr = new Error(`HTTP ${res.status}: ${text.slice(0, 60)}`); }
      else {
        const { code, msg, count } = parseCount(text);
        // 03 = NODATA → 0
        if (code === "03" || /NODATA/i.test(msg)) return 0;
        if (code === "00" || /NORMAL/i.test(msg)) {
          if (count == null || Number.isNaN(count)) throw new Error(`카운트 파싱 실패: ${text.slice(0, 120)}`);
          return count;
        }
        // 그 외 에러코드는 즉시 실패(재시도 무의미: 키/파라미터 오류)
        throw new Error(`API 오류 code=${code} msg=${msg} body=${text.slice(0, 120)}`);
      }
    } catch (e) {
      lastErr = e;
    }
    if (attempt < retries) await sleep(baseDelay * Math.pow(2, attempt)); // 0.8s,1.6s,3.2s,6.4s
  }
  throw new Error(`요청 실패(재시도 ${retries}회 소진): ${lastErr?.message}`);
}

// ---- CSV 헬퍼 (long format) ----
// 컬럼: month,dim,code,label,maker,count
export const CSV_HEADER = "month,dim,code,label,maker,count";

function csvEscape(s) {
  const v = String(s ?? "");
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

export function rowsToCsv(rows) {
  return [CSV_HEADER, ...rows.map((r) =>
    [r.month, r.dim, r.code, r.label, r.maker ?? "", r.count].map(csvEscape).join(",")
  )].join("\n") + "\n";
}

export function readMaster() {
  const p = path.join(ROOT, "data", "ev_master.csv");
  if (!fs.existsSync(p)) return [];
  const lines = fs.readFileSync(p, "utf8").split(/\r?\n/).filter(Boolean);
  if (lines.length <= 1) return [];
  return lines.slice(1).map((line) => {
    // 단순 CSV 파서 (따옴표 처리)
    const cells = [];
    let cur = "", q = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (q) {
        if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (c === '"') q = false;
        else cur += c;
      } else {
        if (c === '"') q = true;
        else if (c === ",") { cells.push(cur); cur = ""; }
        else cur += c;
      }
    }
    cells.push(cur);
    const [month, dim, code, label, maker, count] = cells;
    return { month, dim, code, label, maker, count: Number(count) };
  });
}

export function writeMaster(rows) {
  const p = path.join(ROOT, "data", "ev_master.csv");
  fs.writeFileSync(p, rowsToCsv(rows), "utf8");
}

// 특정 월의 지정 dim(들)만 교체 후 저장 (소스별 멱등 갱신).
// 예: API 수집기는 ['total','fuel','region'] 을, 다나와 수집기는 ['model'] 을 소유.
export function upsertMonthDims(month, ownedDims, newRows) {
  const master = readMaster().filter(
    (r) => !(r.month === month && ownedDims.includes(r.dim))
  );
  const merged = [...master, ...newRows].sort((a, b) =>
    a.month === b.month ? a.dim.localeCompare(b.dim) : a.month.localeCompare(b.month)
  );
  writeMaster(merged);
  return newRows.length;
}

// ---- 다나와 판매실적 스크래핑 (모델 차원) ----
// Tab=Model, Nation=domestic|export(수입), Month=YYYY-MM-00
async function fetchDanawaTab(ym, nation) {
  const month = `${ym.slice(0, 4)}-${ym.slice(4, 6)}-00`;
  const url = `https://auto.danawa.com/newcar/?Work=record&Tab=Model&Nation=${nation}&Month=${month}`;
  const res = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" },
    signal: AbortSignal.timeout(30000),
  });
  if (!res.ok) throw new Error(`다나와 HTTP ${res.status} (${nation})`);
  const html = await res.text();
  const t = html.indexOf("recordTable model");
  if (t < 0) return [];
  const seg = html.slice(t, html.indexOf("</table>", t));
  const rows = [];
  for (const tr of seg.split(/<tr>/).slice(1)) {
    const id = tr.match(/value='record_(\d+)'/)?.[1];
    const name = tr.match(/title='([^']+)'\s+brand=/)?.[1];
    const num = tr.match(/class='num'>\s*([\d,]+)/)?.[1];
    if (id && name && num) {
      rows.push({ id, name: name.trim(), count: Number(num.replace(/,/g, "")), nation });
    }
  }
  return rows;
}

// 국산+수입 모델 판매 전체를 {id: {name, count, nation}} 로 반환
export async function fetchDanawaModels(ym) {
  const [dom, imp] = await Promise.all([
    fetchDanawaTab(ym, "domestic"),
    fetchDanawaTab(ym, "export"),
  ]);
  const map = {};
  for (const r of [...dom, ...imp]) map[r.id] = r;
  return map;
}
