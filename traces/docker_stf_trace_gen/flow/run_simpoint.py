#!/usr/bin/env python3
"""Performs SimPoint analysis on BBV files located under /outputs/<emulator>/<workload>/<benchmark>/bbv."""
import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple
from utils.util import log, LogLevel, run_cmd, ensure_dir, validate_tool, read_file_lines, file_exists

def find_bbv_files(emulator: str, workload: str) -> Dict[str, Path]:
    base = Path("/outputs") / emulator / workload
    if not base.exists():
        log(LogLevel.ERROR, f"No outputs for {emulator}/{workload} under /outputs", fatal=True)
    bbv_files = {}
    for bench_dir in base.iterdir():
        if not bench_dir.is_dir():
            continue
        name = bench_dir.name
        spike_path = bench_dir / "bbv" / f"{name}.bbv_cpu0"
        qemu_path = bench_dir / "bbv" / f"{name}.bbv.0.bb"
        cand = spike_path if spike_path.exists() else qemu_path
        if cand.exists() and cand.stat().st_size > 0:
            bbv_files[name] = cand
    if not bbv_files:
        log(LogLevel.ERROR, "No valid BBV files found", fatal=True)
    return bbv_files

def run_simpoint_analysis(bbv_file: Path, benchmark: str, max_k: int, output_dir: Path) -> Tuple[bool, Path, Path]:
    """Run SimPoint analysis on a BBV file."""
    ensure_dir(output_dir)
    simpoints = output_dir / f"{benchmark}.simpoints"
    weights = output_dir / f"{benchmark}.weights"
    cmd = ["simpoint", "-loadFVFile", str(bbv_file), "-maxK", str(max_k), "-saveSimpoints", str(simpoints), "-saveSimpointWeights", str(weights)]
    success, _, _ = run_cmd(cmd, timeout=300)
    return success and simpoints.exists() and weights.exists(), simpoints, weights

def parse_simpoint_results(simpoints_file: Path, weights_file: Path) -> List[Tuple[int, float]]:
    """Parse SimPoint mapping of interval index -> weight.

    Formats (typical SimPoint):
      - simpoints: "<interval_index> <cluster_id>"
      - weights:   "<weight> <cluster_id>"

    We join on cluster_id to produce a list of (interval_index, weight).
    """
    if not simpoints_file.exists() or not weights_file.exists():
        return []

    # Map cluster_id -> interval_index
    sim_map: Dict[int, int] = {}
    for line in read_file_lines(simpoints_file):
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            interval_idx = int(parts[0])
            cluster_id = int(parts[1])
            sim_map[cluster_id] = interval_idx
        except ValueError:
            continue

    # Map cluster_id -> weight
    weight_map: Dict[int, float] = {}
    for line in read_file_lines(weights_file):
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            weight = float(parts[0])
            cluster_id = int(parts[1])
            weight_map[cluster_id] = weight
        except ValueError:
            continue

    # Join by cluster_id
    out: List[Tuple[int, float]] = []
    for cluster_id, interval_idx in sim_map.items():
        if cluster_id in weight_map:
            out.append((interval_idx, weight_map[cluster_id]))
    # Sort by interval index for reproducibility
    out.sort(key=lambda x: x[0])
    return out

def generate_summary(results: Dict[str, Dict], output_file: Path):
    """Generate SimPoint analysis report."""
    successful = sum(1 for r in results.values() if r['success'])
    total_simpoints = sum(r.get('simpoints_count', 0) for r in results.values() if r['success'])
    lines = [
        f"SimPoint Analysis Summary - {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total Benchmarks: {len(results)}",
        f"Successful: {successful}/{len(results)}",
        f"Average SimPoints: {total_simpoints/max(successful,1):.1f}",
        "\nResults:"
    ]
    for bench, res in results.items():
        lines.append(f"{bench}: {'SUCCESS' if res['success'] else 'FAILED'}")
        if res['success']:
            lines.append(f"  SimPoints: {res['simpoints_count']}")
            lines.append(f"  Coverage: {res.get('coverage', 'N/A')}")
            if res.get('intervals'):
                top = sorted(res['intervals'], key=lambda x: x[1], reverse=True)[:3]
                lines.append(f"  Top intervals: {', '.join(f'{i}({w:.3f})' for i, w in top)}")
    
    with output_file.open('w') as f:
        f.write('\n'.join(lines))
    log(LogLevel.INFO, '\n'.join(lines))

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run SimPoint analysis on BBV files")
    parser.add_argument("--emulator", required=True, choices=["spike", "qemu"])
    parser.add_argument("--workload", required=True, help="Workload suite (e.g., embench-iot)")
    parser.add_argument("--max-k", type=int, default=30, help="Max clusters for SimPoint")
    parser.add_argument("--output-dir", default="/outputs/simpoint_analysis")
    parser.add_argument("--clean-first", action="store_true", help="Remove existing .simpoints/.weights for this workload's benchmarks before analysis")
    args = parser.parse_args()

    validate_tool("simpoint")
    
    output_dir = ensure_dir(Path(args.output_dir))
    log(LogLevel.INFO, f"Starting SimPoint analysis for {args.emulator}")
    
    bbv_files = find_bbv_files(args.emulator, args.workload)
    results = {}
    for bench, bbv_file in bbv_files.items():
        # Optionally remove existing outputs for this benchmark only
        if args.clean_first:
            try:
                (output_dir / f"{bench}.simpoints").unlink(missing_ok=True)  # type: ignore[arg-type]
                (output_dir / f"{bench}.weights").unlink(missing_ok=True)    # type: ignore[arg-type]
            except Exception:
                pass
        log(LogLevel.INFO, f"Analyzing {bench}")
        success, simpoints, weights = run_simpoint_analysis(bbv_file, bench, args.max_k, output_dir)
        result = {'success': success, 'bbv_file': str(bbv_file), 'simpoints_file': str(simpoints), 'weights_file': str(weights)}
        if success:
            intervals = parse_simpoint_results(simpoints, weights)
            result.update({'intervals': intervals, 'simpoints_count': len(intervals), 'coverage': f"{sum(w for _, w in intervals):.3f}"})
        results[bench] = result
    
    summary_file = output_dir / "simpoint_summary.txt"
    generate_summary(results, summary_file)
    with (output_dir / "simpoint_results.json").open('w') as f:
        json.dump(results, f, indent=2)


    if True: #reduce:
        # if spike 
            # gene simpoint manuallly 
        #if qemu :geneerated  using the plugin 
        # test the generated traces agisns teahc other 

        #warmup cache size , must also be done for spike 
        #parse the weights and simpoitn files to get the top 3 intervals and their weights
        # and then slice the traces accodinlgy for that interval from the trace
        # make sure to tweak around with diff intervl_sizes (btw ) give optionf or that 
        #then save the trace corresponding to the top 3 intervals in seperate files 
        # in the format required by the perf model and traace-archive.py tool
        pass

    
    log(LogLevel.INFO, f"Analysis completed. Summary: {summary_file}")

if __name__ == "__main__":
    main()
