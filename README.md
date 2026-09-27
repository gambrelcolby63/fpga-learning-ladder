# FPGA Learning Ladder

Four FPGA projects, from "blink a serial port" to "parse a UDP market-data frame". Together they
form a ladder toward **low-latency / HFT FPGA engineering**. Each rung comes with a written
spec, a skeleton module with the exact port list, and a thorough **cocotb** testbench with a
Python reference model. **The RTL is the part I write myself.**

| # | Project | You build | New concepts |
|---|---|---|---|
| 01 | [`01-uart`](01-uart/) | UART TX + RX, 8N1, parameterized clock/baud | counters, FSMs, valid/ready, oversampling, 2-flop synchronizer, framing errors |
| 02 | [`02-async-fifo`](02-async-fifo/) | dual-clock FIFO | clock-domain crossing, metastability, Gray code, full/empty with stale pointers |
| 03 | [`03-crc32`](03-crc32/) | Ethernet CRC-32 over 64-bit AXI-Stream, one beat/clock | AXI-Stream, `tkeep`, parallel CRC, reflection, residue check |
| 04 | [`04-eth-parser`](04-eth-parser/) | Ethernet/IPv4/UDP header parser + payload realigner | protocol parsing across beat boundaries, realignment, backpressure. The stepping stone to ITCH/MoldUDP64 |

> **About this repo / honesty note.** The specs, testbenches, skeleton files, scripts and CI
> are a provided scaffold (generated with AI assistance). The scaffold intentionally contains
> **no solution RTL**. Everything inside each project's `rtl/` directory beyond the skeleton
> is my own work, and `git log` shows how it evolved. If I used hints, they're recorded in the
> progress checklist below.

---

## Setup

You need: Verilator **≥ 5.036** (cocotb 2.x requirement), Python 3.10+, cocotb 2.1, GNU make.
Optional: Icarus Verilog (second simulator), Yosys (synthesis experiments), GTKWave or
[Surfer](https://surfer-project.org/) (waveform viewer).

### Ubuntu / Debian (and WSL2)

```bash
git clone https://github.com/gambrelcolby63/fpga-learning-ladder.git
cd fpga-learning-ladder
./setup.sh          # apt packages, Verilator 5.052 from source -> /opt/verilator-5.052, .venv with cocotb
```

Distro Verilator packages are too old for cocotb 2.x (Ubuntu 24.04 ships 5.020), so the script
builds 5.052 from source once (~5–15 min). The Makefiles automatically put
`/opt/verilator-5.052/bin` first on `PATH`. Point `VERILATOR_BIN_DIR=...` elsewhere if needed.

**Windows:** install WSL2 with Ubuntu 24.04
(<https://learn.microsoft.com/en-us/windows/wsl/install>), clone the repo **inside the Linux
filesystem** (e.g. `~/src`, not `/mnt/c/...`, where builds are very slow), and run `./setup.sh`.
For waveforms, either install `gtkwave` in WSL (WSLg shows Linux GUI apps on Windows 11) or
open the `.fst` files with Surfer on Windows.

### macOS (Homebrew)

```bash
brew install verilator icarus-verilog yosys surfer   # Homebrew's verilator is 5.052 as of Sep 2026
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
verilator --version                                  # must be >= 5.036
```

(GTKWave is available as a cask, `brew install --cask gtkwave`, but Surfer tends to work better
on Apple Silicon.)

### Check the install

```bash
cd 01-uart
make lint     # warns about unused inputs in the skeleton: expected
make test     # builds and runs; every test FAILS on the skeleton: expected
```

---

## How to work through the ladder

Do the projects **in order**. Each one uses ideas from the previous one. For each project:

1. **Read the project README end to end.** Draw the block diagram and a timing diagram on
   paper before writing any RTL.
2. **Delete the `LADDER-SKELETON` line** at the top of each file in `rtl/` (see [CI](#ci) below).
3. Write the RTL in `rtl/`. Commit early and often. Small commits with honest messages are
   good evidence of your own work.
4. `make lint`: Verilator `-Wall` must be clean. Warnings are bugs until proven otherwise.
5. `make test`: start with one module or test:
   `make test TOP=uart_tx`, `make test TESTCASE=test_directed_bytes`.
6. Debug with waveforms: `make wave` (or `make wave TOP=uart_rx`) writes FST files under
   `build/`, and the command prints the path. Open with `gtkwave file.fst` or `surfer file.fst`.
7. When everything passes: answer the **interview questions** in the project README out loud or
   in writing (a `NOTES.md` in the project folder is a good place), then try a **stretch goal**.
8. Tick the checklist below.

Useful knobs (all projects):

| Command | Effect |
|---|---|
| `make test SEED=1234` | reproduce a randomized run (the seed is printed at the top of each test log) |
| `make test TOP=uart_rx` | only one top-level module |
| `make test TESTCASE=test_bubbles` | only one cocotb test function |
| `make test SIM=icarus` | use Icarus Verilog instead of Verilator (slower; supports less SystemVerilog) |
| `make test LADDER_SCALE=5` | 5× longer randomized tests (soak) |
| `make test RTL_DIR=other/dir` | simulate RTL from a different directory |

**Rules of the road**

* Don't edit the provided testbenches to make them pass. If you think a test is wrong, write
  down why (and open an issue on the repo). You're encouraged to **add your own** test files
  in `tb/`, which is part of learning verification.
* Keep the port lists (and, in 02, the four required internal register names) exactly as given.
* The top-level `make test` / `make status` run everything.

---

## Hint policy

Each project has a `HINTS.md` with collapsible, **progressive** hints:

* **Level 1:** structure and approach. **Level 2–3:** specific techniques for the hard parts.
  **Level 4:** debugging checklist for common failures.
* Hints never contain a full solution. You won't find complete modules anywhere in this repo.
* Get stuck for at least **30–60 minutes** (with waveforms open) before opening the next level.
* Record which hint levels you used in the checklist. It keeps your "I built this" statement
  precise, and it's a good record of what you found hard.

---

## CI

`.github/workflows/ci.yml` runs on every push: it builds (and caches) Verilator 5.052, then runs
`make lint` and `make test` for **each project as a separate job**, and writes a per-project
table into the run's summary page.

**Skeleton marker:** every `rtl/*.sv` file starts with a `// LADDER-SKELETON` line. While that
line is present in any of a project's RTL files, the project counts as *not started*. CI still
runs its lint and tests and reports the numbers, but that job stays green. When you start a
project, delete the marker from its files. From then on that job **fails** unless lint is clean
and all tests pass. So:

* a fresh clone → all four jobs green, all reported as "not started";
* you're halfway through project 02 → job 02 is red, which is honest;
* you finish project 02 → job 02 is green and enforced.

Why this design: a CI that's red on day one just trains you to ignore it, while one that's green
because it skips everything proves nothing. The marker makes "started" an explicit decision.
Run the same check locally with `scripts/ci_project.sh 02-async-fifo` or `make status`.

---

## Progress checklist

Copy this into your own notes or tick it here in commits.

### 01-uart
- [ ] Read spec, drew timing diagrams
- [ ] `uart_tx`: lint clean, all tests pass (4 configs)
- [ ] `uart_rx`: lint clean, all tests pass (3 configs)
- [ ] Answered the 10 interview questions (NOTES.md)
- [ ] Stretch goal(s) done: ______
- Hints used: L1 ☐ L2 ☐ L3 ☐ L4 ☐

### 02-async-fifo
- [ ] Read spec + Cummings paper
- [ ] Lint clean, all tests pass (3 configs × 5 clock scenarios)
- [ ] Can derive the Gray-code full condition on a whiteboard
- [ ] Answered the interview questions
- [ ] Stretch goal(s) done: ______
- Hints used: L1 ☐ L2 ☐ L3 ☐ L4 ☐

### 03-crc32
- [ ] Bit-serial CRC in Python matches `zlib.crc32`
- [ ] Lint clean, all tests pass
- [ ] Looked at synthesis results (Yosys) for logic depth
- [ ] Answered the interview questions
- [ ] Stretch goal(s) done: ______
- Hints used: L1 ☐ L2 ☐ L3 ☐ L4 ☐

### 04-eth-parser
- [ ] Byte-offset map for IHL=5 and IHL=6 drawn by hand
- [ ] Header capture working (single UDP test headers match)
- [ ] Payload realignment working, all tests pass, lint clean
- [ ] Measured stall cycles / latency (`test_throughput` log)
- [ ] Answered the interview questions
- [ ] Stretch goal(s) done: ______ (MoldUDP64/ITCH is the natural next project)
- Hints used: L1 ☐ L2 ☐ L3 ☐ L4 ☐

---

## Describing this work honestly (e.g. on a resume)

Good: *"Implemented a dual-clock async FIFO (Gray-code pointers, 2-flop synchronizers) and a
64-bit AXI-Stream Ethernet/IPv4/UDP parser in SystemVerilog; verified against randomized cocotb
testbenches (Verilator), lint-clean under `-Wall`."* Link the repo.

Be ready to say which parts you wrote (the RTL, plus any tests you added) and which were
provided (the specs and testbenches). Interviewers respect that, and they *will* ask you to
explain your RTL line by line.

---

## Repo layout

```
setup.sh                 toolchain setup (Ubuntu/Debian/WSL)
requirements.txt         Python deps (cocotb)
Makefile                 top-level: test / lint / status across all projects
common/run.py            builds + runs one cocotb test module (used by every project Makefile)
common/project.mk        shared make targets: test, lint, wave, clean
common/ladder_tb.py      shared testbench helpers (AXI-Stream source/sink with protocol checks)
scripts/ci_project.sh    lint + test + report for one project (CI uses this)
NN-name/
  README.md              the spec
  HINTS.md               progressive hints
  Makefile               test configurations (parameters) and lint configurations
  rtl/*.sv               YOUR CODE (skeleton to start)
  tb/*.py                cocotb testbench + Python reference model
```

## Troubleshooting

* **`Verilator 5.0xx found; cocotb 2.x needs >= 5.036`**: run `./setup.sh`, or set
  `VERILATOR_BIN_DIR` to a newer Verilator's `bin/`.
* **`No module named cocotb`**: the Makefiles use `.venv/bin/python` if it exists, otherwise
  `python3`. Create the venv (setup.sh does), or pass `PYTHON=/path/to/python`.
* **A test hangs**: every test has a simulated-time timeout, so it will fail eventually. Run
  it alone with `TESTCASE=` and look at the waveform.
* **`internal signal 'wptr_gray' not found`** (project 02): the spec requires those exact register names.
* **Icarus errors on valid SystemVerilog**: Icarus supports a subset of SV. Verilator is the
  reference simulator here.

## Tool references

cocotb docs <https://docs.cocotb.org/en/stable/> · Verilator guide <https://verilator.org/guide/latest/> ·
Icarus Verilog <https://steveicarus.github.io/iverilog/> · Yosys <https://github.com/YosysHQ/yosys> ·
WaveDrom (timing diagrams in the READMEs) <https://wavedrom.com/> · HDLBits (Verilog practice
problems, great warm-up before project 01) <https://hdlbits.01xz.net/>
