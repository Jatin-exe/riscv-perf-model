#!/usr/bin/env python3
"""Builds RISC-V workloads (short, consistent paths) or links custom objects with the environment wrapper.
Defaults: /default; Custom: /workloads; Env: /environment
Outputs (obj+bin): /outputs/<emulator>/bin/<workload>/<benchmark>/

New modes:
- Config-driven build with --workload as before
- Wrapper-link mode with --input-obj/--input-elf and optional --entrypoint mapping to benchmark()
"""
import os
import argparse
from pathlib import Path
from typing import List
from utils.util import log, LogLevel, run_cmd, ensure_dir, file_exists
from utils.config import BoardConfig

DEFAULT_WORKLOADS = {
    "embench-iot": "embench-iot",
    "riscv-tests": "riscv-tests",
    "dhrystone": "riscv-tests",
    "coremark": "coremark"
}

DEFAULT_ROOT = Path("/default")
WORKLOADS_ROOT = Path("/workloads")
ENV_ROOT = Path("/environment")
OUTPUTS_ROOT = Path("/outputs")

class WorkloadBuilder:
    """Manages building of RISC-V workloads."""
    def __init__(self, emulator: str, arch: str, platform: str, bbv: bool, trace: bool):
        self.emulator = emulator
        self.arch = arch
        self.platform = platform
        self.bbv = bbv
        self.trace = trace
        self.config = BoardConfig(emulator)
        self.env_dir = ENV_ROOT / emulator
        self.executables = []

    def _get_flags(self, config: dict, workload_path: Path, workload_type: str, benchmark: str = None) -> tuple:
        """Get compiler and linker flags."""
        build_config = self.config.get_build_config(self.arch, self.platform, workload_type, self.bbv, self.trace, benchmark)
        cc = build_config.get('cc')
        cflags = build_config.get('base_cflags', []) + build_config.get('cflags', [])
        cflags.extend(f"-D{define}" for define in build_config.get('defines', []))
        # Normalize -I/workloads/... includes to existing path; fallback to /default if needed
        norm_cflags = []
        for flag in cflags:
            if isinstance(flag, str) and flag.startswith('-I'):
                inc = Path(flag[2:])
                if not inc.exists() and str(inc).startswith('/workloads/'):
                    alt = Path(str(inc).replace('/workloads/', '/default/', 1))
                    flag = f"-I{alt}" if alt.exists() else flag
            norm_cflags.append(flag)
        cflags = norm_cflags
        cflags.extend(f"-I{inc}" for inc in self.config.get_workload_includes(workload_path, workload_type))
        # Add common support include if present (embench)
        support_dir = workload_path / 'support'
        if support_dir.exists():
            cflags.append(f"-I{support_dir}")
        # Env headers
        cflags.append(f"-I{workload_path}/env")
        # Deduplicate flags while preserving order
        seen = set()
        deduped = []
        for f in cflags:
            if f not in seen:
                deduped.append(f)
                seen.add(f)
        cflags = deduped
        if self.platform == "baremetal":
            cflags.append(f"-I{self.env_dir}")
        ldflags = build_config.get('base_ldflags', [])
        if os.environ.get("DEBUG"):
            log(LogLevel.DEBUG, f"CONFIG: cc={cc} cflags={cflags} ldflags={ldflags}")
        return cc, cflags, ldflags, build_config

    def build_environment(self, workload: str):
        """Compile environment runtime files."""
        if self.config.should_skip_environment(self.platform, workload):
            log(LogLevel.INFO, f"Skipping environment build for {self.platform}")
            return
        # Pick workload root: prefer /workloads/<workload>, else /default/<workload>
        wroot = WORKLOADS_ROOT / workload
        workload_path = wroot if wroot.exists() else (DEFAULT_ROOT / workload)
        cc, cflags, _, _ = self._get_flags({}, workload_path, workload)
        env_files = list(self.config.get_environment_files(workload))
        # Ensure util.c for custom baremetal to satisfy tohost/printn
        if workload == "custom" and self.platform == "baremetal":
            if "util.c" not in env_files:
                env_files.append("util.c")
        for src in env_files:
            src_file = self.env_dir / src
            if src_file.exists():
                env_obj_dir = ensure_dir(OUTPUTS_ROOT / self.emulator / "bin" / "env")
                obj = env_obj_dir / f"{Path(src).stem}.o"
                ok, _, err = run_cmd([cc, "-c", *cflags, "-o", str(obj), str(src_file)])
                if not ok:
                    log(LogLevel.ERROR, f"Env compile failed: {err}", fatal=True)

    def build_common_files(self, workload_path: Path, workload_type: str) -> List[str]:
        """Compile common files for riscv-tests family (includes dhrystone)."""
        if workload_type not in ("riscv-tests", "dhrystone") or not (common_dir := workload_path / "benchmarks" / "common").exists():
            return []
        cc, cflags, _, _ = self._get_flags({}, workload_path, workload_type)
        skip = self.config.get_skip_common_files(self.platform, workload_type)
        obj_files = []
        for c_file in common_dir.glob("*.c"):
            if c_file.name in skip:
                continue
            # Place common objects under /outputs/<emu>/bin/<workload>/common
            common_obj_dir = ensure_dir(OUTPUTS_ROOT / self.emulator / "bin" / ("riscv-tests" if workload_type == "dhrystone" else workload_type) / "common")
            obj = common_obj_dir / f"{c_file.stem}.o"
            ok, _, _ = run_cmd([cc, "-c", *cflags, "-o", str(obj), str(c_file)])
            if ok:
                obj_files.append(str(obj))
        return obj_files

    def build_benchmark(self, bench: str, workload_path: Path, workload_type: str, common_objs: List[str]):
        """Compile and link a single benchmark."""
        log(LogLevel.INFO, f"Building {bench}")
        bench_dir = workload_path / ("src" if workload_type == "embench-iot" else "benchmarks") / bench
        if not bench_dir.exists():
            log(LogLevel.ERROR, f"Benchmark directory not found: {bench_dir}", fatal=True)
        
        # Find source files
        source_exts = ['.c'] if workload_type == "embench-iot" else ['.c', '.S']
        sources = [f for ext in source_exts for f in bench_dir.glob(f"*{ext}")]
        if not sources:
            log(LogLevel.ERROR, f"No sources found for {bench}", fatal=True)
        
        # Compile sources
        cc, cflags, ldflags, config = self._get_flags({}, workload_path, workload_type, bench)
        obj_files = []
        for src in sources:
            out_dir = ensure_dir(OUTPUTS_ROOT / self.emulator / "bin" / workload_type / bench / "obj")
            obj = out_dir / f"{src.stem}.o"
            ok, _, err = run_cmd([cc, "-c", *cflags, "-o", str(obj), str(src)])
            if ok:
                obj_files.append(str(obj))
            else:
                log(LogLevel.ERROR, f"Compile failed: {err}", fatal=True)
        
        # Compile additional sources for embench-iot
        if workload_type == "embench-iot":
            for src in config.get('workload_sources', []):
                src_path = Path(src)
                if not src_path.exists() and str(src_path).startswith('/workloads/'):
                    try:
                        rel = src_path.relative_to('/workloads')
                        alt = Path('/default') / rel
                        if alt.exists():
                            src_path = alt
                    except Exception:
                        pass
                if src_path.exists():
                    out_dir = ensure_dir(OUTPUTS_ROOT / self.emulator / "bin" / workload_type / bench / "obj")
                    obj = out_dir / f"{src_path.stem}_support.o"
                    ok, _, err = run_cmd([cc, "-c", *cflags, "-o", str(obj), str(src_path)])
                    if ok:
                        obj_files.append(str(obj))
                    else:
                        log(LogLevel.ERROR, f"Support compile failed: {err}", fatal=True)
        
        # Link executable
        if common_objs:
            obj_files.extend(common_objs)
        exe = ensure_dir(OUTPUTS_ROOT / self.emulator / "bin" / workload_type / bench) / bench
        link_cmd = [cc, *ldflags, "-o", str(exe), *obj_files]
        if self.platform == "baremetal":
            lds_name = config.get('linker_script', 'link.ld')
            lds_file = self.env_dir / lds_name
            if not lds_file.exists():
                log(LogLevel.ERROR, f"Linker script not found: {lds_file}", fatal=True)
            env_obj_dir = ensure_dir(OUTPUTS_ROOT / self.emulator / "bin" / "env")
            env_objs = [str(env_obj_dir / f"{Path(f).stem}.o") for f in self.config.get_environment_files(workload_type)]
            link_cmd.extend([f"-T{lds_file}", *env_objs])
        link_cmd.extend(config.get('libs', []))
        ok, _, err = run_cmd(link_cmd)
        if ok:
            self.executables.append(str(exe))
        else:
            log(LogLevel.ERROR, f"Link failed: {err}", fatal=True)

    def list_benchmarks(self, workload_path: Path, workload_type: str) -> List[str]:
        """List available benchmarks for a workload."""
        dir_path = workload_path / ("src" if workload_type == "embench-iot" else "benchmarks")
        if not dir_path.exists():
            return []
        return [d.name for d in dir_path.iterdir() if d.is_dir() and (workload_type != "riscv-tests" or d.name != "common")]

    def build_workload(self, workload: str, benchmark: str = None, custom_path: str = None):
        """Build specified workload or benchmark."""
        workload_name = DEFAULT_WORKLOADS.get(workload, "riscv-tests")
        if custom_path:
            workload_path = Path(custom_path)
        else:
            wroot = WORKLOADS_ROOT / workload_name
            workload_path = wroot if wroot.exists() else (DEFAULT_ROOT / workload_name)
        workload_type = workload if workload in DEFAULT_WORKLOADS else "custom"
        if not file_exists(workload_path):
            log(LogLevel.ERROR, f"Workload path not found: {workload_path}", fatal=True)
        
        log(LogLevel.INFO, f"Building {workload} for {self.arch}/{self.platform}/{self.emulator}")
        self.build_environment(workload_type)
        common_objs = self.build_common_files(workload_path, workload_type)
        benchmarks = [benchmark] if benchmark else (["dhrystone"] if workload == "dhrystone" else self.list_benchmarks(workload_path, workload_type))

        # Fallback for single-program workloads (e.g., coremark) with configured sources
        if not benchmarks:
            cc, cflags, ldflags, cfg = self._get_flags({}, workload_path, workload_type)
            sources = cfg.get('workload_sources', [])
            if sources:
                bench = workload
                log(LogLevel.INFO, f"Building single-program workload: {bench}")
                obj_dir = ensure_dir(OUTPUTS_ROOT / self.emulator / "bin" / workload_type / bench / "obj")
                obj_files: List[str] = []
                for src in sources:
                    sp = Path(src)
                    if not sp.is_absolute():
                        sp = workload_path / src
                    if sp.exists():
                        obj = obj_dir / f"{sp.stem}.o"
                        ok, _, err = run_cmd([cc, "-c", *cflags, "-o", str(obj), str(sp)])
                        if not ok:
                            log(LogLevel.ERROR, f"Compile failed: {err}", fatal=True)
                        obj_files.append(str(obj))
                exe = ensure_dir(OUTPUTS_ROOT / self.emulator / "bin" / workload_type / bench) / bench
                link_cmd = [cc, *ldflags, "-o", str(exe), *obj_files]
                if self.platform == "baremetal":
                    lds_name = cfg.get('linker_script', 'link.ld')
                    lds_file = self.env_dir / lds_name
                    if not lds_file.exists():
                        log(LogLevel.ERROR, f"Linker script not found: {lds_file}", fatal=True)
                    env_obj_dir = ensure_dir(OUTPUTS_ROOT / self.emulator / "bin" / "env")
                    env_objs = [str(env_obj_dir / f"{Path(f).stem}.o") for f in self.config.get_environment_files(workload_type)]
                    link_cmd.extend([f"-T{lds_file}", *env_objs])
                link_cmd.extend(cfg.get('libs', []))
                ok, _, err = run_cmd(link_cmd)
                if not ok:
                    log(LogLevel.ERROR, f"Link failed: {err}", fatal=True)
                self.executables.append(str(exe))
                log(LogLevel.INFO, f"Built 1 executable: {exe}")
                return

        for bench in benchmarks:
            self.build_benchmark(bench, workload_path, workload_type, common_objs)
        log(LogLevel.INFO, f"Built {len(self.executables)} executables")

def main():
    """Main entry point for building workloads or linking custom inputs."""
    parser = argparse.ArgumentParser(description="Build RISC-V workloads or link custom objects with environment wrapper")
    src_group = parser.add_mutually_exclusive_group(required=False)
    src_group.add_argument("--workload", help="Workload name (embench-iot | riscv-tests | dhrystone | custom)")
    src_group.add_argument("--input-obj", nargs='+', help="One or more object/archive files to link (e.g., foo.o or libfoo.a)")
    src_group.add_argument("--input-elf", help="Already-linked ELF (baremetal/linux). Wrapper relink is not supported; prefer --input-obj for wrapper.")
    parser.add_argument("--arch", default="rv32", choices=["rv32", "rv64"])
    parser.add_argument("--platform", default="baremetal", choices=["baremetal", "linux"])
    parser.add_argument("--emulator", default="spike", choices=["spike", "qemu"])
    parser.add_argument("--benchmark", help="Specific benchmark")
    parser.add_argument("--custom-path", help="Custom workload path")
    parser.add_argument("--bbv", action="store_true", help="Enable BBV support")
    parser.add_argument("--trace", action="store_true", help="Enable tracing")
    parser.add_argument("--list", action="store_true", help="List available workloads")
    parser.add_argument("--entrypoint", default=None, help="Custom entry function to call from wrapper (default: benchmark(); 'main' also supported)")
    args = parser.parse_args()

    if args.list and not (args.input_obj or args.input_elf):
        for name, _ in DEFAULT_WORKLOADS.items():
            wname = DEFAULT_WORKLOADS[name]
            wpath = (WORKLOADS_ROOT / wname) if (WORKLOADS_ROOT / wname).exists() else (DEFAULT_ROOT / wname)
            if file_exists(wpath):
                log(LogLevel.INFO, f"{name}: {wpath}")
                builder = WorkloadBuilder(args.emulator, args.arch, args.platform, args.bbv, args.trace)
                benchmarks = builder.list_benchmarks(Path(wpath), name)
                if benchmarks:
                    log(LogLevel.INFO, f"  Benchmarks: {', '.join(benchmarks[:10])}{'...' if len(benchmarks) > 10 else ''}")
        return

    builder = WorkloadBuilder(args.emulator, args.arch, args.platform, args.bbv, args.trace)

    # Custom object/ELF linking mode
    if args.input_obj or args.input_elf:
        # Compile environment
        builder.build_environment("custom")

        # Optional wrapper to map entrypoint -> benchmark()
        entry = (args.entrypoint or "benchmark").strip()
        wrapper_obj = None
        if entry and entry not in ("benchmark",):
            wrapper_src = f"""
            extern int {entry}(void);
            int benchmark(void) {{ return {entry}(); }}
            """
            out_dir = ensure_dir(OUTPUTS_ROOT / args.emulator / "bin" / "custom" / "wrapper")
            src_file = out_dir / "wrapper_entry.c"
            src_file.write_text(wrapper_src)
            cc, cflags, _, _ = builder._get_flags({}, Path('/default'), 'custom')
            wrapper_obj = out_dir / "wrapper_entry.o"
            ok, _, err = run_cmd([cc, "-c", *cflags, "-o", str(wrapper_obj), str(src_file)])
            if not ok:
                log(LogLevel.ERROR, f"Wrapper compile failed: {err}", fatal=True)

        # Link
        name = (Path(args.input_obj[0]).stem if args.input_obj else Path(args.input_elf).stem)
        exe_dir = ensure_dir(OUTPUTS_ROOT / args.emulator / "bin" / "custom" / name)
        exe = exe_dir / name
        build_cfg = builder.config.get_build_config(args.arch, args.platform, "custom", args.bbv, args.trace)
        cc = build_cfg.get('cc')
        ldflags = build_cfg.get('base_ldflags', [])
        env_obj_dir = ensure_dir(OUTPUTS_ROOT / args.emulator / "bin" / "env")
        env_objs = [str(env_obj_dir / f"{Path(f).stem}.o") for f in builder.config.get_environment_files("custom") if (env_obj_dir / f"{Path(f).stem}.o").exists()]
        link_cmd = [cc, *ldflags, "-o", str(exe)]
        if args.platform == "baremetal":
            lds_name = build_cfg.get('linker_script', 'link.ld')
            lds_file = builder.env_dir / lds_name
            if not lds_file.exists():
                log(LogLevel.ERROR, f"Linker script not found: {lds_file}", fatal=True)
            link_cmd.append(f"-T{lds_file}")
        if args.input_obj:
            link_cmd.extend(args.input_obj)
        elif args.input_elf:
            log(LogLevel.WARN, "Linking a pre-linked ELF into the wrapper is not supported; attempting to link directly.")
            link_cmd.append(args.input_elf)
        if wrapper_obj:
            link_cmd.append(str(wrapper_obj))
        link_cmd.extend(env_objs)
        link_cmd.extend(build_cfg.get('libs', []))
        ok, _, err = run_cmd(link_cmd)
        if not ok:
            log(LogLevel.ERROR, f"Link failed: {err}", fatal=True)
        log(LogLevel.INFO, f"Custom executable built: {exe}")
        return

    # Config-driven build mode
    if not args.workload:
        log(LogLevel.ERROR, "Must provide --workload or --input-obj/--input-elf", fatal=True)
    builder.build_workload(args.workload, args.benchmark, args.custom_path)

if __name__ == "__main__":
    main()
