# 섹션⑥ 신규/철거 리포트 월간 오케스트레이터 — 스냅샷 ingest→analyze→verify(게이트)→charger_deployment.json 배치
"""
AEP 월간 GitHub Action에서 build_dashboard_data.py 뒤에 실행.

흐름:
  1. (기본) data/raw/ 최신 raw parquet → build_snapshots --ingest (당월 스냅샷 append·prune)
     --no-ingest 면 스킵(현재 스냅샷 스토어 그대로 사용; 시드 산출물 발행용)
  2. charger_report/analyze.py  → output/report_data.json (+ 분석 xlsx)
  3. charger_report/verify.py   → 마지막 줄 '✅ 전체 검증 통과' 아니면 비0 종료(배포 게이트)
  4. report_data.json → data/charger_deployment.json (대시보드 fetch 대상)
"""
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CR_DIR = REPO_ROOT / "scripts" / "charger_report"
RAW_DIR = REPO_ROOT / "data" / "raw"
OUT_JSON = CR_DIR / "output" / "report_data.json"
PUBLISH = REPO_ROOT / "data" / "charger_deployment.json"
PASS_LINE = "✅ 전체 검증 통과"


def run(args, **kw):
    print(f"\n$ {' '.join(str(a) for a in args)}")
    return subprocess.run(args, **kw)


def latest_raw() -> Path | None:
    cands = sorted(RAW_DIR.glob("raw_*.parquet"))
    return cands[-1] if cands else None


def main():
    no_ingest = "--no-ingest" in sys.argv
    py = sys.executable

    if not no_ingest:
        raw = latest_raw()
        if raw is None:
            print("[warn] data/raw 에 raw_*.parquet 없음 → ingest 스킵 (스냅샷 스토어만 사용)")
        else:
            r = run([py, "-X", "utf8", str(CR_DIR / "build_snapshots.py"), "--ingest", str(raw)])
            if r.returncode != 0:
                sys.exit("ingest 실패")
    else:
        print("[--no-ingest] 현재 스냅샷 스토어로 분석만 수행")

    if run([py, "-X", "utf8", str(CR_DIR / "analyze.py")]).returncode != 0:
        sys.exit("analyze 실패")

    # verify: stdout 캡처해 마지막 라인으로 게이트 판정
    vr = run([py, "-X", "utf8", str(CR_DIR / "verify.py")], capture_output=True, text=True, encoding="utf-8")
    print(vr.stdout)
    if vr.stderr:
        print(vr.stderr, file=sys.stderr)
    last = (vr.stdout or "").strip().splitlines()[-1].strip() if vr.stdout else ""
    if vr.returncode != 0 or last != PASS_LINE:
        sys.exit(f"❌ verify 게이트 실패 (마지막 줄: {last!r}) → 발행 중단")

    shutil.copyfile(OUT_JSON, PUBLISH)
    print(f"\n✅ 발행: {PUBLISH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
