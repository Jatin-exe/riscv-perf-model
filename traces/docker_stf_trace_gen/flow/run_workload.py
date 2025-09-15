#!/usr/bin/env python3
"""Runs RISC-V workloads on Spike or QEMU, generating outputs in /outputs/<emulator>/<workload>/<benchmark>/..."""
import argparse
from pathlib import Path
from typing import Dict
import os
from utils.util import log, LogLevel, run_cmd, get_time, validate_tool, clean_dir, ensure_dir
import json

OUTPUTS_ROOT = Path("/outputs")
from utils.config import BoardConfig

def run_emulator(binary: Path, dirs: Dict[str, Path], emulator: str, bbv: bool, trace: bool, platform: str, arch: str, interval_size: int,
                 enable_stf_tools: bool, isa: str, qemu_trace_num: int, qemu_trace_start: int) -> float:
    """Run workload on emulator."""
    name = binary.stem
    log(LogLevel.INFO, f"Running {name} on {emulator.upper()} ({platform}/{arch})")
    # ISA string used by spike
    
    # Derive plugin paths
    qemu_plugins_dir = os.environ.get("QEMU_PLUGINS", "/qemu/build/contrib/plugins")
    bbv_plugin = os.path.join(qemu_plugins_dir, "libbbv.so")
    stf_plugin = "/usr/lib/libstfmem.so"

    configs = {
        "spike": {
            "cmd": ["spike", f"--isa={isa}"],
            "bbv": lambda: ["--en_bbv", f"--bb_file={dirs['bbv'] / f'{name}.bbv'}", f"--simpoint_size={interval_size}"],
            "trace": lambda: ["--stf_macro_tracing", "--stf_trace_memory_records", f"--stf_trace={dirs['traces'] / f'{name}.zstf'}"],
            "bin": [str(binary)]
        },
        "qemu": {
            "cmd": (["qemu-system-riscv32" if arch == "rv32" else "qemu-system-riscv64", "-nographic", "-machine", "virt", "-bios", "none", "-kernel", str(binary)]
                    if platform == "baremetal" else
                    [f"qemu-riscv{32 if arch == 'rv32' else 64}", str(binary)]),
            "bbv": lambda: ( ["-plugin", f"{bbv_plugin},interval={interval_size},outfile={dirs['bbv'] / f'{name}.bbv'}"] if os.path.exists(bbv_plugin) else []),
            "trace": lambda: ( ["-plugin", f"{stf_plugin},mode=dyn_insn_count,start_dyn_insn={qemu_trace_start},num_instructions={qemu_trace_num},outfile={dirs['traces'] / f'{name}.zstf'}", "-d", "plugin"] if os.path.exists(stf_plugin) else []),
        }
    }
    
    cfg = configs[emulator]
    cmd = cfg["cmd"] + (cfg["bbv"]() if bbv else []) + (cfg["trace"]() if trace else []) + cfg.get("bin", [])
    # build the logs file 
    start = get_time() 
    ok, out, err = run_cmd(cmd)
    # Write emulator outputs to logs
    try:
        (dirs['logs'] / f"{name}.{emulator}.stdout.txt").write_text(out or "")
        (dirs['logs'] / f"{name}.{emulator}.stderr.txt").write_text(err or "")
    except Exception:
        pass
    end = get_time()
    
    if emulator == "spike" and trace and enable_stf_tools:
        trace_file = dirs['traces'] / f"{name}.zstf"
        if trace_file.exists():
            stf_dump = Path("/riscv/stf_tools/release/tools/stf_dump/stf_dump")
            if stf_dump.exists():
                run_cmd([str(stf_dump), str(trace_file)])
    
    return end - start

from typing import Optional

def run_workloads(emulator: str, platform: str, arch: str, bbv: bool, trace: bool, workload: str, benchmark: Optional[str],
                  interval_size: int = 10**7, enable_stf_tools: bool = False,
                  qemu_trace_num: int = 1000000, qemu_trace_start: int = 0,
                  binary: Optional[str] = None, clean: bool = False):
    """Run all workloads from binary list."""
    # Validate required tools
    tools = []
    if emulator == 'spike':
        tools = ['spike']
    else:
        tools = [f"qemu-{'system-' if platform == 'baremetal' else ''}riscv{32 if arch == 'rv32' else 64}"]
    if not validate_tool(tools):
        log(LogLevel.ERROR, f"Missing tools: {tools}", fatal=True)

    # Determine ISA from config (for Spike)
    bc = BoardConfig(emulator)
    build_cfg = bc.get_build_config(arch, platform)
    isa = build_cfg.get('arch')
    if not isa:
        # Fallback: extract from -march flag
        for f in build_cfg.get('base_cflags', []):
            if isinstance(f, str) and f.startswith('-march='):
                isa = f.split('=', 1)[1]
                break
    if not isa:
        isa = f"rv{32 if arch == 'rv32' else 64}imafdc"
    
    exe_list = []
    custom_mode = False
    if binary:
        # Explicit binary path
        bpath = Path(binary)
        if not bpath.exists():
            log(LogLevel.ERROR, f"Binary not found: {bpath}", fatal=True)
        exe_list = [(bpath, 'custom', bpath.stem)]
        custom_mode = True
    else:
        # Discover binaries under /outputs/<emulator>/bin/<workload>/
        base = OUTPUTS_ROOT / emulator / 'bin' / workload
        if not base.exists():
            log(LogLevel.ERROR, f"No built binaries found under {base}. Build first.", fatal=True)
        bench_dirs = [d for d in base.iterdir() if d.is_dir()]
        if benchmark:
            bench_dirs = [d for d in bench_dirs if d.name == benchmark]
            if not bench_dirs:
                log(LogLevel.ERROR, f"Benchmark not found: {benchmark}", fatal=True)
        for bench_dir in bench_dirs:
            exe = bench_dir / bench_dir.name
            if not exe.exists():
                log(LogLevel.ERROR, f"Executable not found: {exe}", fatal=True)
            exe_list.append((exe, workload, bench_dir.name))

    total_time = 0.0
    for exe, wl, benchname in exe_list:
        # Prepare output directories for this run
        out_workload = ('custom' if custom_mode else wl)
        base_out = ensure_dir(OUTPUTS_ROOT / emulator / out_workload / benchname)
        # Optional targeted clean of only the dirs we will write to
        if clean:
            try:
                from utils.util import clean_dir as _clean
                for sub in ('logs', 'bbv', 'traces'):
                    _clean(base_out / sub)
            except Exception:
                pass
        dirs = {'base': base_out, 'logs': ensure_dir(base_out / 'logs'), 'bbv': ensure_dir(base_out / 'bbv'), 'traces': ensure_dir(base_out / 'traces')}
        # Persist run metadata for downstream tools (e.g., sliced trace generation)
        try:
            meta = {
                'emulator': emulator,
                'platform': platform,
                'arch': arch,
                'isa': isa,
                'interval_size': interval_size,
                'bbv_enabled': bool(bbv),
                'trace_enabled': bool(trace),
            }
            (base_out / 'run_meta.json').write_text(json.dumps(meta, indent=2) + "\n")
        except Exception:
            pass
        try:
            run_time = run_emulator(exe, dirs, emulator, bbv, trace, platform, arch, interval_size, enable_stf_tools, isa, qemu_trace_num, qemu_trace_start)
            total_time += run_time
        except RuntimeError:
            continue
    log(LogLevel.INFO, f"Completed {len(exe_list)} benchmark runs. Total time {total_time:.2f}s")

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run RISC-V workloads")
    parser.add_argument("--emulator", required=True, choices=["spike", "qemu"], help="Emulator to use")
    parser.add_argument("--platform", default="baremetal", choices=["baremetal", "linux"], help="Platform type")
    parser.add_argument("--arch", default="rv32", choices=["rv32", "rv64"], help="Architecture")
    parser.add_argument("--bbv", action="store_true", help="Enable BBV generation")
    parser.add_argument("--trace", action="store_true", help="Generate STF Traces (Spike only)")
    parser.add_argument("--workload", help="Workload suite (e.g., embench-iot). Optional when using --binary")
    parser.add_argument("--benchmark", help="Specific benchmark name (optional)")
    parser.add_argument("--interval-size", type=int, default=10**7, help="BBV Interval size")
    parser.add_argument("--enable-stf-tools", action="store_true")
    parser.add_argument("--trace-num-instructions", type=int, default=1000000, help="QEMU STF: number of instructions")
    parser.add_argument("--trace-start-instruction", type=int, default=0, help="QEMU STF: start instruction index")
    parser.add_argument("--clean", action="store_true", help="Clean only the target benchmark output subdirs (logs, bbv, traces) before running")
    parser.add_argument("--binary", help="Explicit ELF/binary path to run (bypasses discovery)")
    args = parser.parse_args()
    
    run_workloads(args.emulator, args.platform, args.arch, args.bbv, args.trace,
                  args.workload or 'custom', args.benchmark, args.interval_size, args.enable_stf_tools,
                  args.trace_num_instructions, args.trace_start_instruction, args.binary, args.clean)

if __name__ == "__main__":
    main()
