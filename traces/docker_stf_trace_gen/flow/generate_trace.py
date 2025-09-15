#!/usr/bin/env python3
"""Generate STF traces, including SimPoint-sliced traces.

Run this script inside the Docker container. It uses outputs under /outputs.
"""
import argparse
import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

from utils.util import ensure_dir, run_cmd, log, LogLevel, read_file_lines

OUTPUTS_ROOT = Path("/outputs")


def load_run_meta(emulator: str, workload: str, benchmark: str) -> Dict:
    meta_path = OUTPUTS_ROOT / emulator / workload / benchmark / "run_meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except Exception:
            return {}
    return {}


def find_binary(emulator: str, workload: str, benchmark: str) -> Path:
    cand = OUTPUTS_ROOT / emulator / "bin" / workload / benchmark / benchmark
    if cand.exists():
        return cand
    log(LogLevel.ERROR, f"Binary not found: {cand}", fatal=True)
    return cand


def parse_simpoints(simpoints_file: Path, weights_file: Path) -> List[Tuple[int, float, int]]:
    """Return list of (interval_index, weight, cluster_id)."""
    if not simpoints_file.exists() or not weights_file.exists():
        log(LogLevel.ERROR, f"Missing simpoint files: {simpoints_file}, {weights_file}", fatal=True)
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
    out: List[Tuple[int, float, int]] = []
    for cid, idx in sim_map.items():
        if cid in weight_map:
            out.append((idx, weight_map[cid], cid))
    out.sort(key=lambda x: x[0])
    if not out:
        log(LogLevel.ERROR, "Parsed no SimPoint intervals", fatal=True)
    return out


def gen_spike_slice(isa: str, binary: Path, out_file: Path, start_inst: int, count: int) -> bool:
    cmd = [
        "spike", f"--isa={isa}",
        "--stf_trace", str(out_file),
        "--stf_trace_memory_records",
        "--stf_insn_num_tracing",
        "--stf_insn_start", str(start_inst),
        "--stf_insn_count", str(count),
        "--stf_stats", str(out_file.with_suffix('.stats.json')),
        str(binary)
    ]
    ok, _, _ = run_cmd(cmd)
    return ok and out_file.exists() and out_file.stat().st_size > 0


def gen_qemu_slice(arch: str, platform: str, binary: Path, out_file: Path, start_inst: int, count: int) -> bool:
    """Minimal QEMU slice generation using trace-gen plugin (linux-user recommended).
    System-mode support is not guaranteed here.
    """
    plugin = "/usr/lib/libstfmem.so"
    if not Path(plugin).exists():
        log(LogLevel.ERROR, f"QEMU STF plugin not found at {plugin}")
        return False
    if platform == "baremetal":
        qemu = f"qemu-system-riscv{64 if arch == 'rv64' else 32}"
        cmd = [
            qemu, "-nographic", "-machine", "virt", "-bios", "none",
            "-plugin", f"{plugin},mode=dyn_insn_count,start_dyn_insn={start_inst},num_instructions={count},outfile={out_file}",
            "-d", "plugin",
            "-kernel", str(binary)
        ]
    else:
        qemu = f"qemu-riscv{64 if arch == 'rv64' else 32}"
        cmd = [
            qemu,
            "-plugin",
            f"{plugin},mode=dyn_insn_count,start_dyn_insn={start_inst},num_instructions={count},outfile={out_file}",
            "-d", "plugin", "--", str(binary)
        ]
    ok, _, _ = run_cmd(cmd)
    return ok and out_file.exists() and out_file.stat().st_size > 0

def try_qemu_simpoint_plugin(arch: str, platform: str, binary: Path, simpoints: Path, weights: Path, interval_size: int, outdir: Path) -> bool:
    plugin = "/usr/lib/libstfmem.so"
    if not Path(plugin).exists():
        return False
    qemu = (f"qemu-system-riscv{64 if arch == 'rv64' else 32}" if platform == "baremetal" else f"qemu-riscv{64 if arch == 'rv64' else 32}")
    base_cmd = [qemu]
    if platform == "baremetal":
        base_cmd += ["-nographic", "-machine", "virt", "-bios", "none", "-kernel", str(binary)]
    else:
        base_cmd += [str(binary)]
    outdir.mkdir(parents=True, exist_ok=True)
    # Attempt a plausible simpoint mode; fallback if fails
    plugin_arg = f"{plugin},mode=simpoint,simpoint_file={simpoints},weights_file={weights},simpoint_size={interval_size},outdir={outdir}"
    cmd = base_cmd[:1] + ["-plugin", plugin_arg, "-d", "plugin"] + base_cmd[1:]
    ok, _, _ = run_cmd(cmd)
    if ok:
        produced = list(outdir.glob("*.zstf"))
        return len(produced) > 0
    return False


def main():
    p = argparse.ArgumentParser(description="Generate STF traces or SimPoint-sliced STF traces")
    p.add_argument("--emulator", required=True, choices=["spike", "qemu"], help="Emulator to use")
    p.add_argument("--workload", required=True, help="Workload suite (e.g., embench-iot)")
    p.add_argument("--benchmark", required=True, help="Benchmark name (e.g., aha-mont64)")
    p.add_argument("--interval-size", type=int, help="Interval size (overrides metadata)")
    p.add_argument("--simpoints", type=Path, help="Path to .simpoints file (optional)")
    p.add_argument("--weights", type=Path, help="Path to .weights file (optional)")
    p.add_argument("--outdir", type=Path, help="Output directory override")
    p.add_argument("--sliced", action="store_true", help="Generate SimPoint-sliced traces")
    p.add_argument("--verify", action="store_true", help="Verify slices with stf_count if available")
    p.add_argument("--dump", action="store_true", help="Run stf_dump on generated traces")
    p.add_argument("--arch", choices=["rv32", "rv64"], help="QEMU arch hint (optional)")
    p.add_argument("--platform", choices=["baremetal", "linux"], help="QEMU platform hint (optional)")
    p.add_argument("--qemu-simpoint-plugin", action="store_true", help="Try QEMU plugin simpoint mode (experimental)")
    p.add_argument("--clean", action="store_true", help="Clean the target simpointed output dir before writing")
    args = p.parse_args()

    binary = find_binary(args.emulator, args.workload, args.benchmark)
    meta = load_run_meta(args.emulator, args.workload, args.benchmark)

    isa = meta.get("isa") or ("rv64imafdc" if (meta.get("arch") == "rv64" or args.arch == "rv64") else "rv32imafdc")
    arch = meta.get("arch") or (args.arch if args.arch else ("rv64" if isa.startswith("rv64") else "rv32"))
    platform = meta.get("platform") or (args.platform if args.platform else "baremetal")
    interval_size = args.interval_size or meta.get("interval_size") or 10**7

    outdir = args.outdir or (OUTPUTS_ROOT / "simpointed" / args.emulator / args.workload / args.benchmark)
    # Optional targeted clean of only this benchmark's simpointed output dir
    if getattr(args, 'clean', False):
        from utils.util import clean_dir as _clean
        _clean(outdir)
    ensure_dir(outdir)

    if not args.sliced:
        # Simple one-shot trace (full interval provided via CLI overrides)
        out_file = outdir / f"{args.benchmark}.zstf"
        if args.emulator == "spike":
            ok = gen_spike_slice(isa, binary, out_file, start_inst=0, count=interval_size)
        else:
            ok = gen_qemu_slice(arch, platform, binary, out_file, start_inst=0, count=interval_size)
        if not ok:
            log(LogLevel.ERROR, "Trace generation failed", fatal=True)
        log(LogLevel.INFO, f"Trace written: {out_file}")
        return

    # SimPoint-sliced generation
    simpoints = args.simpoints or (OUTPUTS_ROOT / "simpoint_analysis" / f"{args.benchmark}.simpoints")
    weights = args.weights or (OUTPUTS_ROOT / "simpoint_analysis" / f"{args.benchmark}.weights")
    slices = parse_simpoints(simpoints, weights)

    # Experimental: QEMU simpoint plugin mode
    if args.emulator == "qemu" and args.qemu_simpoint_plugin:
        success = try_qemu_simpoint_plugin(arch, platform, binary, simpoints, weights, int(interval_size), outdir)
        if success:
            # Build manifest from produced files (best-effort: map by filename suffix)
            manifest = []
            for interval_idx, weight, cluster_id in slices:
                outfile = outdir / f"{args.benchmark}.sp_{interval_idx}.zstf"
                if outfile.exists():
                    manifest.append({
                        "interval_index": interval_idx,
                        "cluster_id": cluster_id,
                        "weight": weight,
                        "start": interval_idx * int(interval_size),
                        "count": int(interval_size),
                        "file": str(outfile)
                    })
            (outdir / "slices.json").write_text(json.dumps(manifest, indent=2) + "\n")
            log(LogLevel.INFO, f"QEMU simpoint plugin produced {len(manifest)} slices")
            return
        else:
            log(LogLevel.WARN, "QEMU simpoint plugin mode failed or produced no slices; falling back to per-interval dyn_insn_count")

    manifest: List[Dict] = []
    stf_count_bin = Path("/riscv/stf_tools/release/tools/stf_count/stf_count")
    stf_dump_bin = Path("/riscv/stf_tools/release/tools/stf_dump/stf_dump")
    for interval_idx, weight, cluster_id in slices:
        start_inst = interval_idx * int(interval_size)
        count = int(interval_size)
        outfile = outdir / f"{args.benchmark}.sp_{interval_idx}.zstf"
        log(LogLevel.INFO, f"Generating slice: idx={interval_idx} (cluster {cluster_id}, w={weight:.6f}) start={start_inst} count={count}")
        if args.emulator == "spike":
            ok = gen_spike_slice(isa, binary, outfile, start_inst, count)
        else:
            ok = gen_qemu_slice(arch, platform, binary, outfile, start_inst, count)
        if not ok:
            log(LogLevel.WARN, f"Slice failed for interval {interval_idx}")
            continue
        entry = {
            "interval_index": interval_idx,
            "cluster_id": cluster_id,
            "weight": weight,
            "start": start_inst,
            "count": count,
            "file": str(outfile)
        }
        if args.verify and stf_count_bin.exists():
            ok, out, _ = run_cmd([str(stf_count_bin), str(outfile)])
            if ok and out:
                try:
                    # Parse: "... inst_count N ..."
                    for tok in out.strip().split():
                        if tok.isdigit():
                            # find the token after 'inst_count'
                            pass
                    # naive parse
                    parts = out.strip().split()
                    if "inst_count" in parts:
                        idx = parts.index("inst_count")
                        inst_count = int(parts[idx+1].replace(",", ""))
                        entry["inst_count"] = inst_count
                        entry["verified"] = (inst_count == count or inst_count == count + 1)
                except Exception:
                    entry["verified"] = False
        if args.dump and stf_dump_bin.exists():
            try:
                dump_path = outfile.with_suffix(".stfdump.txt")
                run_cmd([str(stf_dump_bin), str(outfile)])
                # Capture to file using shell redirection not available; run_cmd already captures stdout, so re-run:
                ok2, out2, _ = run_cmd([str(stf_dump_bin), str(outfile)])
                if ok2 and out2:
                    dump_path.write_text(out2)
            except Exception:
                pass
        manifest.append(entry)

    (outdir / "slices.json").write_text(json.dumps(manifest, indent=2) + "\n")
    log(LogLevel.INFO, f"Generated {len(manifest)}/{len(slices)} slices; manifest: {outdir / 'slices.json'}")


if __name__ == "__main__":
    main()
