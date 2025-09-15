Porting Workloads

Overview
- Two ways to integrate a workload:
  1) Config-driven build via YAML (recommended for suites)
  2) Wrapper-link an existing object/archive with a custom entrypoint

YAML-driven build (recommended)
- Describe compiler, flags, includes, libs, environment files, and workload-specific overrides in `environment/<board>/board.yaml`.
- The schema is hierarchical:
  - defaults: base toolchain and flags
  - architectures.<rv32|rv64>.platforms.<baremetal|linux>: arch/platform specific flags
  - workloads.<suite>: workload-specific flags and sources
  - workloads.<suite>.platforms.<platform>: platform-specific overrides
  - features.{bbv,trace}: optional flags injected when enabled
- List-type fields can be YAML lists or space-separated strings; both are supported.

CoreMark tutorial (YAML route)
1) Place CoreMark at `./workloads/coremark` or rely on `/default/coremark` in the image
2) Add entries to `environment/spike/board.yaml` under `workloads.coremark` with required `workload_cflags`, optional `workload_sources`, and platform overrides
3) Build and run inside the container:
   - Build:  `python3 flow/build_workload.py --workload coremark --arch rv32 --platform baremetal --emulator spike --bbv --trace`
   - Run:    `python3 flow/run_workload.py --emulator spike --arch rv32 --platform baremetal --workload coremark --bbv --interval-size 10000`
4) SimPoint and slicing:
   - `python3 flow/run_simpoint.py --emulator spike --workload coremark`
   - `python3 flow/generate_trace.py --emulator spike --workload coremark --benchmark coremark --sliced --verify`

Wrapper-link route (prebuilt objects)
- If you have compiled objects (e.g., `coremark.o`, `libcoremark.a`) and want to use the environment wrapper without editing sources:
  - Build:  `python3 flow/build_workload.py --input-obj coremark.o --entrypoint coremark_main --arch rv32 --platform baremetal --emulator spike`
    - This compiles the environment and a tiny wrapper that maps `benchmark()` to `coremark_main()`.
  - Run:    `python3 flow/run_workload.py --emulator spike --arch rv32 --platform baremetal --binary /outputs/spike/bin/custom/coremark/coremark --bbv --interval-size 10000`
  - Then SimPoint + slicing as usual.

Notes
- For baremetal, ensure objects are built with a newlib toolchain and matching `-march/-mabi` to the board config.
- For ELF binaries, prefer running directly with `run_workload.py --binary`. Wrapper relinking of a prelinked ELF is not supported; provide .o/.a instead if wrapper is needed.

