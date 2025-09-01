#!/usr/bin/env python3
"""Performs SimPoint analysis on BBV files."""
import argparse
import os
import docker


def get_qemu_cmd(args, trace_docker_path: str, workload_docker_path: str):

    if args.start_instruction is not None:
        args.start_instruction += 1

    if args.mode == "insn_count":
        return f"qemu-riscv64 -plugin /usr/lib/libstfmem.so,mode=dyn_insn_count,start_dyn_insn={args.start_instruction},num_instructions={args.num_instructions},outfile={trace_docker_path} -d plugin -- {workload_docker_path}"
    elif args.mode == "pc_count":
        return f"qemu-riscv64 -plugin /usr/lib/libstfmem.so,mode=ip,start_ip={args.start_pc},ip_hit_threshold={args.pc_threshold},num_instructions={args.num_instructions},outfile={trace_docker_path} -d plugin -- {workload_docker_path}"

    raise NotImplementedError()

def get_spike_cmd(args, trace_docker_path: str, workload_docker_path: str):    
    if args.mode == "insn_count":
        return f"spike --isa=rv64imafdc_zba_zbb_zbc_zbs_zicntr_zicond_zifencei --stf_trace {trace_docker_path} --stf_trace_memory_records --stf_insn_num_tracing --stf_insn_start {str(args.start_instruction)} --stf_insn_count {str(args.num_instructions)}  {workload_docker_path}"
    elif args.mode == "macro":
        return f"spike --isa=rv64imafdc_zba_zbb_zbc_zbs_zicntr_zicond_zifencei --stf_trace {trace_docker_path} --stf_trace_memory_records --stf_macro_tracing {workload_docker_path}"
    # if args.mode == "insn_count":
    #     return f"spike --isa=rv32imafdc --stf_trace {trace_docker_path} --stf_trace_memory_records --stf_insn_num_tracing --stf_insn_start {args.start_instruction} --stf_insn_count {args.num_instructions}  {workload_docker_path}"
    # elif args.mode == "macro":
    #     return f"spike --isa=rv32imafdc --stf_trace {trace_docker_path} --stf_trace_memory_records --stf_macro_tracing {workload_docker_path}"
    else:
        raise ValueError(f"Invalid mode ({args.mode}) provided")

def run_trace(args):
    host_workload_path = os.path.abspath(args.workload)
    host_workload_folder = os.path.dirname(host_workload_path)
    host_workload_filename = os.path.basename(host_workload_path)

    docker_folder = "/test"
    docker_workload_path = f"{docker_folder}/{host_workload_filename}"
    docker_trace_path = f"{docker_workload_path}.zstf"

    bash_cmd = ""
    if args.emulator == "spike":
        bash_cmd = get_spike_cmd(args, docker_trace_path, docker_workload_path)
    elif args.emulator == "qemu":
        bash_cmd = get_qemu_cmd(args, docker_trace_path, docker_workload_path)
    else:
        raise ValueError(f"Invalid emulator ({args.emulator}) provided")

    image = "riscv-perf-model:latest"

    print('bash_cmd')
    print(bash_cmd)
    client = docker.from_env()
    container = client.containers.run(
        image=image,
        command=["bash", "-c", bash_cmd],
        volumes={
            host_workload_folder: {"bind": docker_folder, "mode": "rw"}
        },
        remove=True,     # equivalent to --rm
        detach=False,    # run and wait
        stdout=True,
        stderr=True
    )

    print(container)

def parse_args():
    parser = argparse.ArgumentParser(description="Generate traces for a workload")
    # parser.add_argument('-h', '--help', action='help', help='Show this help message and exit.')
    parser.add_argument("--emulator", required=True, choices=["spike", "qemu"])
    parser.add_argument("--mode", required=True, choices=["macro", "insn_count", "pc_count"])

    parser.add_argument("--num-instructions", type=int, help="Number of instructions to trace")
    parser.add_argument("--start-instruction", type=int, default=0, help="Number of instructions to skip before tracing (insn_count mode)")

    parser.add_argument("--start-pc", type=lambda x: int(x, 0), help="Starting program counter (pc_count mode)")
    parser.add_argument("--pc-threshold", type=int, default=1, help="PC hit threshold (pc_count mode)")

    parser.add_argument("workload", help="Path to workload file")

    # TODO add output
    # parser.add_argument("output_file", help="Path to save the generated trace")

    args = parser.parse_args()

    if args.mode == "insn_count":
        if args.num_instructions is None or args.start_instruction is None:
            parser.error("--num-instructions and --start-instruction are required when --mode insn_count")

    elif args.mode == "pc_count":
        if args.emulator == "spike":
            parser.error("pc_count mode can't be used with spike")

        if args.num_instructions is None or args.start_pc is None or args.pc_threshold is None:
            parser.error("--num-instructions, --start-pc, --pc-threshold are required when --mode pc_count")

    elif args.mode == "macro" and args.emulator == "qemu":
        parser.error("macro mode can't be used with qemu")

    return args

def main():
    """Main entry point."""
    args = parse_args()
    run_trace(args)

    

if __name__ == "__main__":
    main()

