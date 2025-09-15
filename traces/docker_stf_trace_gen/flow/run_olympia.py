#!/usr/bin/env python3
"""Run Olympia on STF traces or SimPoint-sliced manifests.

Usage examples (inside container):
  # Run on a single STF
  python3 flow/run_olympia.py --trace /outputs/simpointed/spike/embench-iot/aha-mont64/aha-mont64.sp_18.zstf

  # Run all .zstf in a directory
  python3 flow/run_olympia.py --dir /outputs/simpointed/spike/embench-iot/aha-mont64

  # Use manifest to run selected slices
  python3 flow/run_olympia.py --manifest /outputs/simpointed/spike/embench-iot/aha-mont64/slices.json
"""
import argparse
import json
from pathlib import Path
from typing import List
from utils.util import run_cmd, ensure_dir, log, LogLevel


def run_olympia_on(trace: Path, report_dir: Path, interval: str | None = None) -> bool:
    ensure_dir(report_dir)
    report = report_dir / (trace.stem + ".report.txt")
    cmd = ["olympia"]
    if interval:
        cmd.append(f"-i{interval}")
    cmd.extend([str(trace), "--report-all", str(report)])
    ok, out, err = run_cmd(cmd)
    if not ok:
        log(LogLevel.WARN, f"Olympia failed on {trace}: {err}")
    return ok and report.exists() and report.stat().st_size > 0


def collect_traces_from_manifest(manifest: Path) -> List[Path]:
    data = json.loads(manifest.read_text())
    files = []
    for e in data:
        f = Path(e.get("file", ""))
        if f.exists() and f.suffix in (".stf", ".zstf"):
            files.append(f)
    return files


def main():
    ap = argparse.ArgumentParser(description="Run Olympia on STF traces")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--trace", type=Path, help="Single STF file")
    src.add_argument("--dir", type=Path, help="Directory of STF files to run")
    src.add_argument("--manifest", type=Path, help="slices.json manifest")
    ap.add_argument("--out", type=Path, default=Path("/outputs/olympia_reports"), help="Output reports dir")
    ap.add_argument("--clean", action="store_true", help="Clean target report dir for the benchmark before writing")
    ap.add_argument("--emulator", choices=["spike", "qemu"], help="For output path layout (optional)")
    ap.add_argument("--workload", help="For output path layout (optional)")
    ap.add_argument("--benchmark", help="For output path layout (optional)")
    ap.add_argument("--interval", type=str, help="Olympia -i value (e.g., 100000 or 100K)")
    args = ap.parse_args()

    traces: List[Path] = []
    if args.trace:
        traces = [args.trace]
        bench = args.trace.parent.name
        work = args.trace.parent.parent.name
        emu = args.trace.parents[2].name if len(args.trace.parents) >= 3 else (args.emulator or "spike")
        out_dir = args.out / emu / work / bench
    elif args.dir:
        traces = sorted([p for p in args.dir.glob("*.zstf")])
        bench = args.dir.name
        work = args.dir.parent.name
        emu = args.dir.parents[2].name if len(args.dir.parents) >= 3 else (args.emulator or "spike")
        out_dir = args.out / emu / work / bench
    else:
        traces = collect_traces_from_manifest(args.manifest)
        man_dir = args.manifest.parent
        bench = man_dir.name
        work = man_dir.parent.name
        emu = man_dir.parents[2].name if len(man_dir.parents) >= 3 else (args.emulator or "spike")
        out_dir = args.out / emu / work / bench

    if args.clean:
        from utils.util import clean_dir as _clean
        _clean(out_dir)
    ensure_dir(out_dir)
    ok_count = 0
    for t in traces:
        if run_olympia_on(t, out_dir, args.interval):
            ok_count += 1
            log(LogLevel.INFO, f"Olympia report written for {t.name}")
    log(LogLevel.INFO, f"Olympia completed: {ok_count}/{len(traces)} traces")


if __name__ == "__main__":
    main()
