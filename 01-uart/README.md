# 01 — UART transmitter and receiver (8N1)

**Goal:** build `uart_tx` and `uart_rx`, a serial transmitter and receiver with configurable clock
frequency and baud rate, using 8 data bits, no parity, 1 stop bit ("8N1").
You'll practice counters, small FSMs, a valid/ready handshake, and your first
**clock-domain crossing**: the `rx` pin is not synchronous to your clock.

Estimated effort: 6–12 hours if you're new to RTL.

Files:

| File | What it is |
|---|---|
| `rtl/uart_tx.sv`, `rtl/uart_rx.sv` | **Your code.** Skeletons with the fixed port lists |
| `tb/test_uart_tx.py`, `tb/test_uart_rx.py` | cocotb testbenches (don't edit them to make tests pass) |
| `tb/uart_model.py` | Python reference model (frame bits, TX trace decoder) |
| `HINTS.md` | Hints that get more specific as you go. Open them only when you're stuck |

```
make test            # all configs, both modules
make test TOP=uart_tx
make lint            # verilator -Wall must be clean
make wave TOP=uart_rx   # FST waveform -> build/.../dump.fst, open with gtkwave or surfer
```

---

## 1. Background

### The frame

When the line is idle it sits at `1`. A frame looks like this, least-significant bit first:

```
 idle | start |  d0  |  d1  |  d2  |  d3  |  d4  |  d5  |  d6  |  d7  | stop | idle / next start
  1   |   0   |  b0  |  b1  |  b2  |  b3  |  b4  |  b5  |  b6  |  b7  |  1   |  1 ...
      |<------------------------- 10 bit periods ------------------------->|
```

* **Start bit (0):** the 1→0 edge tells the receiver that a frame is starting. That edge is the
  only timing reference the receiver gets, because the two ends share no clock ("asynchronous" serial).
* **Data bits:** 8 bits, LSB first.
* **Stop bit (1):** puts the line back to idle so the next start edge can be seen. If the
  receiver samples the stop bit and reads `0`, the frame is broken: that's a **framing error**.
  It happens when the baud rates don't match, the line is noisy, or the far end sends a
  **break** (holds the line low for longer than a frame).

One bit period is `CLKS_PER_BIT` clock cycles:

```systemverilog
localparam int CLKS_PER_BIT = (CLK_FREQ_HZ + BAUD / 2) / BAUD;   // rounded to nearest
```

Examples: 1.8432 MHz / 115200 → 16. 100 MHz / 3 Mbaud → 33. 10 MHz / 115200 → 87 (86.8
rounded; that's a 0.2 % rate error, which is fine). 50 MHz / 115200 → 434.

### Oversampling and where to sample

The receiver doesn't know the transmitter's phase, so it **oversamples**: it looks at the line
many times per bit. Classic UART chips sample at 16× the baud rate. In an FPGA you usually have a
fast clock anyway, so you can oversample at the full clock rate. Look at `rx` every cycle, and
count `CLKS_PER_BIT` cycles per bit.

1. Wait for a falling edge (idle 1 → 0).
2. Wait **half a bit** (`CLKS_PER_BIT/2`) and look again. If the line is back at 1, it was a
   glitch (noise), not a start bit, so go back to idle. This is **false-start rejection**.
3. You're now in the middle of the start bit. Wait one full bit period at a time and sample each
   data bit **in its middle**, then the stop bit.

Why the middle? The two ends' clocks never match exactly. The receiver re-synchronises on every
start edge, but inside a frame the error builds up bit after bit, and by the stop bit (9.5 bit
periods after the edge) it's at its worst. Sampling in the middle gives ±½ bit of margin for
that drift: about ±5 % of total clock mismatch in theory, and less in practice once you count
synchronizer delay and edge-detection uncertainty. (See the Analog Devices note in the reading list.)

A note on 16× "tick" designs: if you divide the clock by `CLKS_PER_BIT/16` to get a 16× tick,
integer rounding can put the bit period off by several percent (e.g. 33/16 → 2, so a 32-clock bit
instead of 33, a 3 % error baked into your receiver). **This spec requires the bit period to be
exactly `CLKS_PER_BIT` clocks**, so count clocks.

### The `rx` input is asynchronous: synchronize it

`rx` comes from outside the chip and can change at any time relative to `clk`. If it changes
right at a clock edge, the first flip-flop that samples it can go **metastable**: its output
hangs between 0 and 1 for an unpredictable time. The standard fix is a **two-flop synchronizer**:
two flip-flops in series, where only the second one's output is used by your logic. This doesn't
eliminate metastability, but it makes the failure rate astronomically small (the extra cycle
lets the first flop resolve). You'll use the same idea again, much more carefully, in project 02.

---

## 2. Specification

### Parameters (both modules)

| Parameter | Default | Meaning |
|---|---|---|
| `CLK_FREQ_HZ` | 50_000_000 | frequency of `clk` |
| `BAUD` | 115_200 | bit rate. Must satisfy `CLKS_PER_BIT >= 16` |

### `uart_tx` ports

| Port | Dir | Width | Description |
|---|---|---|---|
| `clk` | in | 1 | clock |
| `rst` | in | 1 | synchronous, active-high reset |
| `s_data` | in | 8 | byte to send |
| `s_valid` | in | 1 | `s_data` is valid |
| `s_ready` | out | 1 | transmitter can take a byte this cycle |
| `tx` | out | 1 | serial output, idles high |
| `busy` | out | 1 | 1 while a frame is on the line |

**Required behavior:**

1. A byte is transferred when `s_valid && s_ready` are both 1 at a rising edge of `clk`
   (the same handshake as AXI-Stream). The upstream may change `s_data` right after the
   handshake, and it carries garbage while `s_valid=0`, **so capture the byte at the handshake**.
2. While `rst` is 1 and after it, `tx` is 1. In the cycle after `rst` is sampled high, `tx`
   must be 1 even if a frame was in progress.
3. Every bit on `tx` (start, 8 data, stop) lasts **exactly `CLKS_PER_BIT` clock cycles**.
   The testbench checks every cycle of every bit.
4. `busy` is 1 for every cycle of every frame, from the first cycle of the start bit through
   the last cycle of the stop bit. It's 0 when idle.
5. Back-to-back: if `s_valid` is held high, consecutive frames must start between
   `10*CLKS_PER_BIT` and `10*CLKS_PER_BIT + 2` cycles apart, so at most 2 idle cycles between
   the stop bit and the next start bit. (Zero is possible: accept the next byte during the last
   cycle of the stop bit.)
6. `s_ready` is 1 when idle after reset.

```
clk      _/‾\_/‾\_/‾\_/‾\_/‾\_/‾\_/‾\_ ... _/‾\_/‾\_/‾\_
s_valid  ___/‾‾‾‾‾\_____________________ ... ____________
s_data   ---< A5  >---garbage------------ ... ------------
s_ready  ‾‾‾‾‾‾‾‾‾\_____________________ ... _____/‾‾‾‾‾‾
tx       ‾‾‾‾‾‾‾‾‾\___________/‾‾‾‾‾‾‾‾‾ ... ‾‾‾‾‾‾‾‾‾‾‾‾   start(0), then d0=1 (0xA5 LSB first) ...
busy     _________/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾ ... ‾‾‾‾‾\______
                  |<-- CLKS_PER_BIT -->|
```

WaveDrom (paste into <https://wavedrom.com/editor.html>). One tick here stands for one *bit
period*, not one clock:

```json
{ "signal": [
  {"name": "s_valid", "wave": "010.........."},
  {"name": "s_data", "wave": "x=x..........", "data": ["0xA5"]},
  {"name": "s_ready", "wave": "1.0.........1"},
  {"name": "tx", "wave": "1.01010.101.."},
  {"name": "busy", "wave": "0.1.........0"}
], "head": {"text": "uart_tx sending 0xA5 (LSB first: 1,0,1,0,0,1,0,1); 1 tick = 1 bit period"} }
```

### `uart_rx` ports

| Port | Dir | Width | Description |
|---|---|---|---|
| `clk` | in | 1 | clock |
| `rst` | in | 1 | synchronous, active-high reset |
| `rx` | in | 1 | serial input, **asynchronous** to `clk` |
| `m_data` | out | 8 | received byte, only meaningful while `m_valid=1` |
| `m_valid` | out | 1 | **1-cycle pulse** per correctly framed byte (no backpressure) |
| `frame_err` | out | 1 | **1-cycle pulse** when the stop bit is sampled as 0 |

**Required behavior:**

1. Synchronize `rx` with (at least) two flip-flops before using it.
2. Start detection on the falling edge. Re-check the line in the middle of the start bit and
   ignore pulses shorter than about 1/3 of a bit (the test sends glitches up to `CLKS_PER_BIT/3`).
3. Sample data bits and the stop bit near the middle of each bit.
4. Stop bit = 1 → pulse `m_valid` for exactly one cycle with the byte on `m_data`.
   Stop bit = 0 → pulse `frame_err` for exactly one cycle, **do not** pulse `m_valid`, and then
   **wait until `rx` is 1 again** before hunting for the next start bit. A long break must
   produce exactly one `frame_err`, not a stream of them.
5. Timing of the result pulse: after the stop bit starts, and no later than the end of the
   stop bit + 4 clocks. (Sampling at mid-stop and reporting right away does this.)
6. Back-to-back frames (one stop bit, zero idle) must work, so be ready for the next start edge
   by the end of the stop bit.
7. **Receiver tolerance.** The testbench's transmitter runs off by up to ±`TOL`, with random
   phase: `TOL` = 2 % for `CLKS_PER_BIT < 32`, 3 % for `< 64`, 3.5 % above that. A design that
   samples accurately at mid-bit, with a couple of cycles of synchronizer delay, passes. One that
   samples early (e.g. at ¼ bit) fails on the slow-transmitter test.
8. Frames that arrive while `rst=1` are ignored.

```
rx        ‾‾‾‾‾\_______/‾‾‾‾‾‾‾\_______ ... _______/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
               |start  |  d0   |  d1        d7    | stop          |
sample points:     ^       ^       ^        ^         ^
                (check)                              (mid-stop)
m_valid   ___________________________________________/‾\___________   <- right after mid-stop
```

---

## 3. What the testbench checks

`test_uart_tx.py` (runs at CLKS_PER_BIT = 16, 33, 87, 434):
`test_reset_and_idle`, `test_directed_bytes` (0x00, 0xFF, 0x55, …), `test_back_to_back`,
`test_random_traffic` (random gaps, garbage on `s_data`), `test_ready_only_when_able`,
`test_reset_mid_frame`. The monitor records `tx` every clock and decodes it offline with
`uart_model.decode_tx_trace`, which enforces exact bit timing.

`test_uart_rx.py` (runs at CLKS_PER_BIT = 16, 33, 87): directed bytes, back-to-back,
fast/slow transmitter at ±TOL, random traffic with random baud error and gaps, glitch rejection,
framing error, break, and "no output during reset". Every result is checked for value, order,
single-cycle pulse, and timing window.

Seeds: each run prints `Seeding Python random module with N`. Reproduce a failure with
`make test SEED=N TOP=uart_rx TESTCASE=test_slow_transmitter`.

---

## 4. Stretch goals

* **Loopback top:** `uart_loopback.sv` that wires `uart_rx` → `uart_tx`, with your own cocotb
  test. If you own a board (e.g. an Arty A7, iCEBreaker, or Tang Nano), put it on hardware and
  talk to it with `screen`/`pyserial`.
* Parity (8E1/8O1) as a parameter, with a `parity_err` output.
* A 16-deep FIFO in front of `uart_tx`.
* Majority-vote sampling (3 samples around mid-bit) and measure how much it improves glitch
  immunity.
* Write a SystemVerilog assertion (SVA) for "tx is high whenever busy is low" and check it
  with Verilator `--assert` or with SymbiYosys formal.

## 5. Interview questions you should be able to answer after this

1. Why is the start bit 0 and the idle state 1? What would break if idle were 0?
2. Derive the maximum baud-rate mismatch an 8N1 receiver sampling mid-bit can tolerate. Which bit
   is the worst case, and why?
3. What is metastability? Why does a two-flop synchronizer help, and what does it *not* protect
   you from? (Hint: multi-bit buses; see project 02.)
4. What's a framing error? What's a break condition, and why must your receiver wait for idle
   after one?
5. How does your design reject a glitch on an idle line?
6. Why does a 16× oversampling tick generator cause trouble at some clock/baud ratios? How
   did you avoid it?
7. In the TX, what goes wrong if you don't register `s_data` at the handshake?
8. Why is the valid/ready handshake rule "a transfer happens when both are high at a clock edge"
   so useful? What rule must a source follow once it raises `valid`?
9. How would you add a FIFO so a CPU can write bursts faster than the line rate?
10. Your receiver works in simulation but drops bytes on hardware. What do you check first?

## 6. Suggested reading (links checked 2026-09-27)

* Wikipedia, *Universal asynchronous receiver-transmitter*:
  <https://en.wikipedia.org/wiki/Universal_asynchronous_receiver-transmitter>
* Analog Devices (Maxim), *Determining Clock Accuracy Requirements for UART Communications*, the
  error-budget math: <https://www.analog.com/en/resources/technical-articles/determining-clock-accuracy-requirements-for-uart-communications.html>
* Nandland, *UART, Serial Port, RS-232 Interface* (beginner-friendly VHDL/Verilog walkthrough;
  read it for concepts **after** you've tried your own design):
  <https://nandland.com/uart-serial-port-module/>
* fpga4fun, *Serial interface (RS-232)*: <https://www.fpga4fun.com/SerialInterface.html>
* ZipCPU, *wbuart32*, a production-quality open-source UART with formal proofs:
  <https://github.com/ZipCPU/wbuart32>
* Wikipedia, *Metastability (electronics)*: <https://en.wikipedia.org/wiki/Metastability_(electronics)>
* cocotb documentation: <https://docs.cocotb.org/en/stable/>
