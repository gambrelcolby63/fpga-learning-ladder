# 02 — Asynchronous (dual-clock) FIFO

**Goal:** build `async_fifo`, a FIFO whose write side and read side run on **two unrelated
clocks**, using Gray-coded pointers and two-flop synchronizers, with correct `w_full` / `r_empty`
flags and parameterized width and depth.

Every real FPGA design that talks to the outside world has one of these: MAC clock → user clock,
ADC clock → processing clock, and so on. Getting clock-domain crossing (CDC) right is one of the
things interviewers probe hardest, because bugs here don't show up in normal simulation.
They show up as a corrupted byte once a week in production.

Estimated effort: 6–10 hours.

| File | What it is |
|---|---|
| `rtl/async_fifo.sv` | **Your code.** Skeleton with fixed ports and the required internal register names |
| `tb/test_async_fifo.py` | cocotb testbench with a scoreboard and pointer checks |
| `HINTS.md` | progressive hints |

```
make test     # WIDTH/ADDR_W = 8/4, 32/2, 12/6, five clock-ratio scenarios each
make lint
make wave
```

---

## 1. Background

### Why you can't just pass a counter across clocks

A FIFO is a RAM plus a write pointer and a read pointer. The writer needs the read pointer (to
know when it's full); the reader needs the write pointer (to know when it's empty). Each pointer
lives in its own clock domain and has to be **sent across** to the other.

The problem: a binary counter changes several bits at once (0111 → 1000 flips all four). The
receiving clock can sample in the middle of that transition, and because each bit's wires and
flops have slightly different delays, it can see any mix of old and new bits: 0000, 1111,
0101... That value is garbage and has nothing to do with the real pointer. Add metastability on
top of that. A two-flop synchronizer protects you from metastability **on one bit**, but it can't
make a multi-bit value arrive coherently.

### Gray code

In **Gray code** consecutive values differ in **exactly one bit**:

| binary | 000 | 001 | 010 | 011 | 100 | 101 | 110 | 111 |
|---|---|---|---|---|---|---|---|---|
| Gray | 000 | 001 | 011 | 010 | 110 | 111 | 101 | 100 |

`gray = bin ^ (bin >> 1)`. If a Gray pointer is sampled mid-change, only one bit is uncertain,
so the receiver sees either the old value or the new value, both of which are real pointer
values. Seeing the old one just makes the flag **pessimistic** for a cycle (the FIFO looks
fuller or emptier than it really is), and that's safe. This only works if the pointer advances
by at most one step per source clock and is launched **directly from a flip-flop** (no
combinational logic between the Gray register and the synchronizer, or glitches break the
one-bit-change property).

### The extra pointer bit

With `DEPTH = 2**ADDR_W` entries, pointers are `ADDR_W+1` bits wide. The low `ADDR_W` bits
address the RAM, and the extra MSB tells "empty" (pointers equal) apart from "full" (the writer
has lapped the reader exactly once):

* **empty** (read side): `rptr == wptr_synchronized`, all bits equal.
* **full** (write side): in *binary*, the addresses are equal and the MSBs differ. In *Gray*, that
  becomes: **the top two bits are inverted and the rest are equal**. Work out why with a
  3-bit example on paper. It's a classic interview question.

This gives you all `DEPTH` entries (not `DEPTH-1`), and the test checks that.

### Why the flags are safe even though they're "late"

The read side sees the write pointer 2–3 `rclk` cycles late. So `r_empty` may say "empty" for a
couple of cycles after data arrived (harmless, just latency), but it can never say "not empty"
when the FIFO is empty. Symmetrically, `w_full` can be late to *de-assert* but is never late to
*assert*, because the writer's own pointer is always current. Pessimism in the right direction
is what makes this design correct.

---

## 2. Specification

### Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `WIDTH` | 8 | bits per entry |
| `ADDR_W` | 4 | `DEPTH = 2**ADDR_W` entries; `ADDR_W >= 2` |

### Ports

| Port | Dir | Width | Domain | Description |
|---|---|---|---|---|
| `wclk` | in | 1 | | write clock |
| `wrst_n` | in | 1 | wclk | active-low reset, asynchronous assert |
| `w_en` | in | 1 | wclk | write request |
| `w_data` | in | WIDTH | wclk | write data |
| `w_full` | out | 1 | wclk | FIFO full, writes are ignored |
| `rclk` | in | 1 | | read clock |
| `rrst_n` | in | 1 | rclk | active-low reset, asynchronous assert |
| `r_en` | in | 1 | rclk | read (pop) request |
| `r_data` | out | WIDTH | rclk | head of the FIFO, **first-word fall-through**: valid whenever `r_empty=0` |
| `r_empty` | out | 1 | rclk | FIFO empty, reads are ignored |

### Required internal registers (exact names, `ADDR_W+1` bits)

The testbench looks at these through the simulator. Name them exactly like this:

| Name | Clock | Meaning |
|---|---|---|
| `wptr_gray` | wclk | Gray code of the number of completed writes (mod `2*DEPTH`) |
| `rptr_gray` | rclk | Gray code of the number of completed reads (mod `2*DEPTH`) |
| `rq2_wptr_gray` | rclk | `wptr_gray` after the **2nd** flop of the synchronizer in the rclk domain |
| `wq2_rptr_gray` | wclk | `rptr_gray` after the **2nd** flop of the synchronizer in the wclk domain |

### Behavior

1. A write happens at a `wclk` rising edge when `w_en && !w_full`. `w_en` while full is
   ignored (no overwrite, no pointer change).
2. A read (pop) happens at an `rclk` rising edge when `r_en && !r_empty`. `r_en` while empty is
   ignored. `r_data` shows the oldest entry whenever `r_empty=0`; after a pop, the next entry
   (if any) appears.
3. `wptr_gray` / `rptr_gray` update on the **same edge** as the write / read they count.
4. The FIFO must hold exactly `DEPTH` entries.
5. Never overflow: `w_full` must be 1 whenever `DEPTH` entries are stored. Never underflow:
   `r_empty` must be 1 whenever no completed write is waiting.
6. **Latency bounds** (checked):
   * write into an empty FIFO → `rq2_wptr_gray` picks it up on the 2nd (or 3rd, if you use a
     3-flop synchronizer) `rclk` edge after the write, and `r_empty` falls on the **2nd to 4th**
     `rclk` edge after the write edge;
   * read from a full FIFO → `wq2_rptr_gray` picks it up on the 2nd/3rd `wclk` edge, and
     `w_full` falls on the **2nd to 4th** `wclk` edge.
7. Reset: after both resets, `r_empty=1`, `w_full=0`, all four pointer registers 0. The test
   asserts both resets together, asynchronously, and releases each one synchronously to its
   own clock. (In a real design you'd add a reset synchronizer. See stretch goals.)

### Timing example (write into an empty FIFO, registered `r_empty`)

```
wclk      _/‾\_/‾\_/‾\_/‾\_/‾\_/‾\_
w_en      _/‾‾‾\____________________
wptr_gray  0   |1                        <- updates on the write edge (W)
                W
rclk      __/‾‾\__/‾‾\__/‾‾\__/‾‾\__/‾‾\__
rq1_wptr   0      |1                     <- 1st rclk edge after W
rq2_wptr   0             |1              <- 2nd rclk edge
r_empty   ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾|________  <- 3rd rclk edge (registered flag) - allowed range 2..4
r_data    xxxxxxxxxxxxxxxxxxxx< D0 >
```

```json
{ "signal": [
  {"name": "wclk", "wave": "p......."},
  {"name": "w_en", "wave": "010....."},
  {"name": "wptr_gray", "wave": "=.=.....", "data": ["0", "1"]},
  {},
  {"name": "rclk", "wave": "P.......", "period": 1, "phase": 0.3},
  {"name": "rq1_wptr_gray", "wave": "=..=....", "data": ["0", "1"]},
  {"name": "rq2_wptr_gray", "wave": "=...=...", "data": ["0", "1"]},
  {"name": "r_empty", "wave": "1....0.."},
  {"name": "r_data", "wave": "x....=..", "data": ["D0"]}
], "head": {"text": "write -> synchronized pointer -> r_empty falls"} }
```

---

## 3. What the testbench checks

A Python scoreboard shadows the FIFO. On **every edge of both clocks** it checks data
order and integrity, overflow/underflow, and that `wptr_gray`/`rptr_gray` equal the Gray code of
the true write/read counts. It also checks that `rq2_wptr_gray`/`wq2_rptr_gray` hold a
plausible (recent) value of the other domain's pointer.

Tests: `test_reset_then_one_word`, `test_fill_and_drain` (exactly `DEPTH` accepted with the
reader stopped), `test_random_traffic` (bursty phases, `w_en` while full, `r_en` while empty),
`test_empty_latency`, `test_full_latency`, `test_reset_during_traffic`. The clock scenarios are
write-faster (4 ns / 10 ns), read-faster (10 / 3 ns), nearly equal (10 / 10.01 ns), same frequency
with random phase, and unrelated (7 / 13.002 ns).

**Honest limitation:** RTL simulation has no metastability and no real wire skew, so a binary
pointer crossed with no synchronizer can look fine functionally. That's exactly why the
test pins down the Gray encoding, the synchronizer depth (via latency), and the register names.
The real proof of CDC correctness is structural: code review, CDC lint tools (Vivado
`report_cdc`, Questa CDC, SpyGlass), and timing constraints. See stretch goals.

---

## 4. Stretch goals

* **Timing constraints:** write the Vivado XDC for this FIFO: `set_max_delay -datapath_only`
  on the Gray pointer crossings, `ASYNC_REG` attributes on the synchronizer flops. Explain why
  `set_false_path` on a Gray bus is subtly wrong (skew between bits).
* Reset synchronizer module (async assert, sync de-assert) for each domain.
* `w_count` / `r_count` (approximate fill level) and `almost_full` with a threshold parameter.
* Registered (non-FWFT) read port as a parameter, and explain the latency difference.
* Formal verification with SymbiYosys, following ZipCPU's async-FIFO article (link below).
* Synthesize with Yosys (`yosys -p "read_verilog -sv rtl/async_fifo.sv; synth -top async_fifo"`)
  and look at what the RAM became.

## 5. Interview questions you should be able to answer after this

1. What is metastability, and what is MTBF for a synchronizer? What makes MTBF better or worse?
2. Why can't you synchronize a binary counter bit by bit? Why does Gray code fix it?
3. Why are the pointers `ADDR_W+1` bits? Derive the Gray-code full condition.
4. Why is it OK that `r_empty` is computed from a *stale* write pointer?
5. What happens if there's combinational logic between the Gray pointer register and the
   synchronizer's first flop?
6. What latency does your FIFO have from write to readable? Where do the cycles go?
7. Gray code works when the pointer changes by at most 1 per source clock. What if the writer
   is 10× faster than the reader? (Trick question: is that a problem? Why not?)
8. How would you constrain this in Vivado? What does `ASYNC_REG` do?
9. When would you use a handshake synchronizer or a pulse synchronizer instead of an async FIFO?
10. What's the difference between an asynchronous reset and a synchronous reset, and why do
    people say "asynchronous assert, synchronous de-assert"?

## 6. Suggested reading (links checked 2026-09-27)

* Clifford E. Cummings, *Simulation and Synthesis Techniques for Asynchronous FIFO Design*
  (SNUG 2002), **the** classic paper; this project follows its "style #1". His papers now live
  in the Paradigm Works technical library (search "Asynchronous FIFO"):
  <https://www.paradigm-works.com/technical-library>.
  The same library has his *Clock Domain Crossing (CDC) Design & Verification Techniques Using
  SystemVerilog* (SNUG 2008) and the reset papers.
* ZipCPU, *Crossing clock domains with an Asynchronous FIFO* (with formal proof):
  <https://zipcpu.com/blog/2018/07/06/afifo.html>
* ZipCPU, *Some simple clock-domain crossing solutions*: <https://zipcpu.com/blog/2017/10/20/cdc.html>
* Ran Ginosar, *Metastability and Synchronizers: A Tutorial* (IEEE D&T 2011):
  <https://webee.technion.ac.il/~ran/papers/Metastability-and-Synchronizers.IEEEDToct2011.pdf>
* fpga4fun, *Crossing clock domains*: <https://www.fpga4fun.com/CrossClockDomain.html>
* Wikipedia, *Gray code*: <https://en.wikipedia.org/wiki/Gray_code>
* AMD, *UltraFast Design Methodology Guide* (UG949), CDC and constraints chapters:
  <https://docs.amd.com/r/en-US/ug949-vivado-design-methodology>
