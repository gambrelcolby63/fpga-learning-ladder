#!/usr/bin/env python3
"""Build one RTL top with Verilator (default) or Icarus and run one cocotb test module on it.

You normally don't call this directly -- each project's Makefile does. Example:

    python ../common/run.py --top uart_tx --src uart_tx.sv --module test_uart_tx \
        --params CLK_FREQ_HZ=1843200,BAUD=115200 [--waves] [--sim icarus] [--rtl-dir rtl]

Exit status: 0 if every test in the module passed, 1 otherwise.
A one-line result is appended to build/summary.txt so `make test` can print a table.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MIN_VERILATOR = (5, 36)  # cocotb 2.x requirement


def verilator_version() -> tuple[int, int] | None:
    exe = shutil.which("verilator")
    if exe is None:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout
    m = re.search(r"Verilator (\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_params(s: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in filter(None, s.split(",")):
        k, v = item.split("=", 1)
        params[k.strip()] = v.strip()
    return params


def count_results(xml_path: Path) -> tuple[int, int]:
    """Return (passed, total) from a cocotb results.xml."""
    if not xml_path.exists():
        return 0, 0
    root = ET.parse(xml_path).getroot()
    total = passed = 0
    for tc in root.iter("testcase"):
        total += 1
        if tc.find("failure") is None and tc.find("error") is None and tc.find("skipped") is None:
            passed += 1
    return passed, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", required=True, help="HDL toplevel module name")
    ap.add_argument("--src", required=True, help="comma-separated RTL files, relative to --rtl-dir")
    ap.add_argument("--module", required=True, help="cocotb test module in tb/ (no .py)")
    ap.add_argument("--params", default="", help="comma-separated NAME=VALUE HDL parameters")
    ap.add_argument("--rtl-dir", default=os.environ.get("RTL_DIR", "rtl"))
    ap.add_argument("--sim", default=os.environ.get("SIM", "verilator"), choices=["verilator", "icarus"])
    ap.add_argument("--waves", action="store_true", help="dump waveforms (FST)")
    ap.add_argument("--testcase", default=os.environ.get("TESTCASE", ""),
                    help="run only these test functions (comma-separated)")
    args = ap.parse_args()

    proj = Path.cwd()
    rtl = Path(args.rtl_dir)
    if not rtl.is_absolute():
        rtl = proj / rtl
    sources = [rtl / f for f in args.src.split(",")]
    for s in sources:
        if not s.exists():
            print(f"ERROR: missing RTL file {s}", file=sys.stderr)
            return 2
    params = parse_params(args.params)
    tag = args.top + ("_" + "_".join(f"{k}{v}" for k, v in params.items()) if params else "")
    build_dir = proj / "build" / f"{args.sim}_{tag}"

    if args.sim == "verilator":
        v = verilator_version()
        if v is None:
            print("ERROR: verilator not found on PATH (run ./setup.sh)", file=sys.stderr)
            return 2
        if v < MIN_VERILATOR:
            print(f"ERROR: Verilator {v[0]}.{v[1]:03d} found; cocotb 2.x needs >= 5.036 (run ./setup.sh)",
                  file=sys.stderr)
            return 2

    from cocotb_tools.runner import get_runner  # imported late so the errors above are friendlier

    build_args: list[str] = []
    if args.sim == "verilator":
        # -Wno-fatal: `make lint` is where warnings count; here we just want a simulation.
        build_args = ["-Wno-fatal", "-Wno-lint", "-Wno-style", "--x-assign", "unique", "--x-initial", "unique"]
        if args.waves and os.environ.get("WAVE_FMT", "fst") != "vcd":
            build_args += ["--trace-fst", "--trace-structs"]  # FST needs liblz4-dev with Verilator 5.05x

    runner = get_runner(args.sim)
    runner.build(
        sources=sources,
        hdl_toplevel=args.top,
        parameters=params,
        build_args=build_args,
        build_dir=build_dir,
        waves=args.waves,
        always=True,
        timescale=("1ns", "1ps"),
    )
    # the runner exports sys.path as PYTHONPATH for the simulator's embedded Python
    sys.path[:0] = [str(proj / "tb"), str(Path(__file__).resolve().parent)]
    extra_env: dict[str, str] = {}
    for k, v in params.items():
        extra_env[f"PARAM_{k}"] = v  # testbenches read their parameters from here
    results_xml = build_dir / "results.xml"
    if results_xml.exists():
        results_xml.unlink()
    try:
        runner.test(
            hdl_toplevel=args.top,
            test_module=args.module,
            test_dir=build_dir,  # sim runs here, so dump.fst lands in build/
            seed=os.environ.get("SEED") or None,
            build_dir=build_dir,
            extra_env=extra_env,
            waves=args.waves,
            testcase=[t for t in args.testcase.split(",") if t] or None,
            results_xml=str(results_xml),
        )
    except SystemExit:
        pass  # a crashed simulation leaves results.xml missing/partial; counted below
    passed, total = count_results(results_xml)
    ok = total > 0 and passed == total
    line = f"{'PASS' if ok else 'FAIL'}  {passed:3d}/{total:<3d} {args.module:<22s} {args.top} {args.params}"
    (proj / "build").mkdir(exist_ok=True)
    with open(proj / "build" / "summary.txt", "a") as f:
        f.write(line + "\n")
    print(line)
    if args.waves:
        for w in list(build_dir.glob("*.fst")) + list(build_dir.glob("*.vcd")):
            print(f"waveform: {w}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
