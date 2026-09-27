# 05 — Fixed-point FIR filter (Q1.15, pipelined MAC, valid/ready)

**Goal:** build `fir_filter`, an `NTAPS`-tap FIR filter (16 by default) for a stream of signed
16-bit samples. It uses signed 16-bit coefficients and a full-precision accumulator, then
**rounds and saturates** back to a 16-bit output. It must be **bit-exact** with a numpy golden
model, accept one sample per clock, and have a **constant, documented latency**.

> **The defense / radar-relevant rung.** Projects 01–04 point toward networking and low-latency
> trading. This one is the core of **radar, radio and electronic-warfare signal processing**.
> Digital down-converters, decimation filters, channelizers, beamformers and radar **pulse
> compression** (a matched filter is an FIR filter whose taps are the time-reversed transmit
> pulse) are all built from fixed-point multiply-accumulates on DSP slices. The questions an
> interviewer at a defense FPGA shop will ask ("what's your Q format?", "how many bits does
> the accumulator need?", "truncate or round?", "how does this map onto DSP48s?", "what's the
> latency?") are exactly what this project makes you answer.

Estimated effort: 8–15 hours. Most of it goes into getting the numbers exactly right; the
control logic is small.

| File | What it is |
|---|---|
| `rtl/fir_filter.sv` | **Your code** |
| `tb/test_fir_filter.py` | cocotb testbench |
| `tb/fir_model.py` | numpy golden model: `fir()`, `lowpass()` (filter design), Q1.15 helpers |
| `HINTS.md` | progressive hints |

```
make test | make lint | make wave
```

---

## 1. Background

### The FIR filter

```
y[n] = h[0]·x[n] + h[1]·x[n-1] + ... + h[N-1]·x[n-N+1]        N = NTAPS
```

Keep the last `N` input samples in a shift register (the *delay line*), multiply each by its
coefficient (*tap*), and add up the products. If the input is a single impulse, the output
walks through `h[0], h[1], …, h[N-1]`, which is why `h` is called the *impulse response*.
Choose `h` and you choose which frequencies pass. `fir_model.lowpass()` designs a low-pass
filter in five lines of numpy (windowed sinc).

### Fixed point: the Qm.n format

FPGAs do DSP in **integers**, and we agree where the binary point is. `Qm.n` = two's complement
with `m` integer bits (including the sign bit) and `n` fraction bits. The integer `v` stands for
`v / 2^n`.

| 16-bit value | as integer | as Q1.15 |
|---|---|---|
| `0x7FFF` | 32767 | +0.999969 (largest; **+1.0 does not exist**) |
| `0x4000` | 16384 | +0.5 |
| `0x0001` | 1 | +0.0000305 (1 LSB = 2⁻¹⁵) |
| `0xC000` | -16384 | -0.5 |
| `0x8000` | -32768 | **-1.0 exactly** |

Samples and coefficients are both Q1.15 here. **Coefficient quantization:** a filter designed
in floating point must be rounded to Q1.15 (`fir_model.to_q15`). That changes the frequency
response slightly: stopband attenuation gets worse, and the DC gain drifts. The test log prints
the DC gain of the quantized low-pass, which comes out a hair above 1.0.

### Bit growth

* **Multiply:** `Qa.b × Qc.d = Q(a+c).(b+d)`. Q1.15 × Q1.15 = **Q2.30, 32 bits**. Why two integer
  bits when both inputs are below 1 in magnitude? Because of one corner case:
  `(-1.0) × (-1.0) = +1.0`, which needs the second integer bit. Every other product fits in 31
  bits. The testbench checks that corner.
* **Add:** a sum of `N` terms can be `N` times larger than one term, so it needs
  **⌈log₂ N⌉ guard bits**. The accumulator is `32 + $clog2(NTAPS)` bits (36 for 16 taps,
  Q6.30), and with that width it **cannot overflow for any inputs and coefficients**.
* **Full precision** means you keep all those bits until the very end and round **once**.
  Rounding each product separately adds `N` small errors, and the result is no longer
  bit-exact.

### Getting back to 16 bits: round, then saturate

The output is Q1.15 again, so 15 fraction bits must go (Q.30 → Q.15) and the extra integer
bits must be dealt with.

1. **Round half up:** add half an output LSB (`2^14`), then **arithmetic** shift right by 15
   (which is `floor`). Plain truncation also floors, but without the `+2^14` it's biased: on
   average it's 0.5 LSB too low. That's a DC offset, and in radar a DC offset shows up
   as a fake zero-Doppler target after the FFT. Rounding half up leaves only a tiny bias on
   exact ties (`+0.5 → 1`, `-0.5 → 0`, `-1.5 → -1`), and the testbench checks these ties exactly.
2. **Saturate:** if the rounded value doesn't fit in `[-32768, +32767]`, clamp it to the
   nearest limit. Dropping the top bits instead makes it **wrap around**: +1.0001 becomes
   -0.9999, a full-scale spike with the wrong sign. Saturation behaves like an amplifier
   clipping. It's still an error, but a gentle one.

Round **first**, then saturate (rounding can push +32767.6 to +32768, which must then
saturate).

### Pipelining, DSP slices and latency

A 16×16 multiplier followed by a 16-input 36-bit adder, all in one clock cycle, might run at
50 MHz. Radar and radio front ends run their DSP at 300–500 MHz. The fix is **pipelining**:
put registers between the multiplier and the adders and between adder levels, so every path
from one register to the next is short. The price is **latency**: the result comes out `L`
cycles later. That's fine as long as `L` is **constant and documented**, because downstream
logic (and other channels of a phased array that must stay time-aligned) rely on it.

FPGAs have hard **DSP slices** for this (Xilinx DSP48E1/E2, Intel variable-precision DSP,
Lattice sysDSP). A DSP48 is roughly *input registers → 25×18 multiplier → M register → 48-bit
adder → P register*, with a dedicated cascade to the next DSP. Your RTL maps onto them well
if it has the same shape: registered operands, a **registered product**, then the add. There
are three classic structures, and all three are allowed here:

* **Direct form + adder tree:** products in parallel, then a pipelined tree of adders. Easy to
  reason about. Latency grows like log₂ N.
* **Transposed form:** the input sample is broadcast to all multipliers and the *partial sums*
  move through a register chain. Latency is constant and small. Maps onto the DSP cascade.
* **Systolic form** (the Xilinx favorite): delay lines on both the samples and the sums. No fabric
  adders at all, just a DSP cascade. Latency grows like N.

### Sample delays vs. pipeline registers

The delay line holds *samples*. It must move **only when a new sample is accepted**. The
pipeline registers hold *partial results*. They move whenever the pipeline advances, carrying
real data or bubbles. Mixing the two up is the classic bug in this project: shifting the delay
line on an idle cycle feeds garbage into the filter. Hold on to that distinction when you add
valid/ready.

---

## 2. Specification

### Ports and parameter

| Port | Dir | Width | Description |
|---|---|---|---|
| `NTAPS` | param | | number of taps, 2..64 (tested: 16, 5, 32) |
| `clk`, `rst` | in | 1 | clock; synchronous, active-high reset |
| `coefs` | in | 16·NTAPS | coefficients, Q1.15: **`h[k] = coefs[16*k +: 16]`**; `h[0]` multiplies the **newest** sample |
| `s_tdata` | in | 16 | input sample, Q1.15 (signed) |
| `s_tvalid`, `s_tready` | in / out | 1 | input handshake: a sample is accepted on a rising edge where both are 1 |
| `m_tdata` | out | 16 | output sample, Q1.15 (signed) |
| `m_tvalid`, `m_tready` | out / in | 1 | output handshake |

**Why the coefficients are a port.** In fielded radar and radio systems coefficients usually
live in a register bank that a processor reloads when the mode changes (different bandwidth,
different pulse). A port also lets the testbench try many coefficient sets, including the
nasty corner cases, without recompiling. `coefs` is **quasi-static**: the testbench only
changes it while `rst=1`, and holds it for at least 2 clocks before releasing reset. Turning
the coefficients into a `parameter` / `$readmemh` ROM is a stretch goal.

### Numeric behavior (bit-exact)

For the `n`-th accepted input sample, with `x[m] = 0` for samples before the first one after
reset:

```
acc[n] = Σ_{k=0}^{NTAPS-1} h[k] · x[n-k]                  exact, signed, no overflow
y[n]   = saturate_to_int16( floor( (acc[n] + 2^14) / 2^15 ) )
```

This is exactly `fir_model.fir(x, h)`, which is `np.convolve` plus `round_sat`.
One output per input sample, in order.

### Flow control

1. Standard valid/ready. While `s_tvalid=0`, `s_tdata` is **junk** (the testbench drives random
   values). Ignore it.
2. Once `m_tvalid=1`, `m_tvalid` and `m_tdata` must hold until the handshake (`m_tready=1`).
3. You may stall the input (`s_tready=0`) whenever the output is backpressured. `s_tready` may
   depend combinationally on `m_tready`.
4. **Throughput:** whenever `m_tready` is held at 1, `s_tready` must be 1 on every cycle
   after reset: one sample per clock, no bubbles.
5. **No sample may be lost, duplicated or reordered** under any pattern of `s_tvalid` gaps and
   `m_tready` backpressure.

### Latency (your choice, but constant)

If a sample is accepted at rising edge `n` and its result is first presented (`m_tvalid=1`) in
the cycle starting at edge `n+L`, the latency is `L`.

* While `m_tready` is held at 1, **every** sample must have the **same** `L`, whatever the gaps
  in `s_tvalid` are.
* `0 ≤ L ≤ NTAPS + 8`. (`L = 0` means your output register loads on the edge that accepts the
  sample, so the whole multiply-add is one combinational path. That's legal here but won't
  meet timing on real hardware.) Typical values: 2–4 for a transposed design, about
  `$clog2(NTAPS) + 2` for a pipelined tree, about `NTAPS + 3` for a systolic chain.
* A sample's result must come out `L` cycles later **even if no further samples arrive**.
  The pipeline must not need the next sample to push it out.
* Write your `L` in a comment at the top of your RTL. The testbench measures it and prints it.

### Reset

Synchronous, active-high, at least one clock. After a rising edge with `rst=1`:
`m_tvalid=0`, anything in flight is discarded (it never comes out), and the filter restarts
with an **all-zero history** (the delay line is cleared). Datapath registers that only carry
values qualified by a valid bit don't need a reset (see the DSP note in HINTS).

### Timing (one possible schedule: NTAPS = 16, L = 3, no backpressure)

```
edge:        1     2     3     4     5     6     7     8
s_tvalid  __/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾\______________________________
s_tdata   --< x0 >< x1 >< x2 >------------------------------   (junk while s_tvalid=0)
s_tready  ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾   (m_tready=1 -> always 1)
m_tvalid  ____________________/‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾\____________
m_tdata   --------------------< y0 >< y1 >< y2 >------------
             ^x0 accepted at edge 2          ^y0 presented from edge 5: L = 3
```

```json
{ "signal": [
  {"name": "clk", "wave": "p........"},
  {"name": "s_tvalid", "wave": "01..0...."},
  {"name": "s_tdata", "wave": "x===x....", "data": ["x0", "x1", "x2"]},
  {"name": "s_tready", "wave": "1........"},
  {"name": "m_tvalid", "wave": "0....1..0"},
  {"name": "m_tdata", "wave": "x....===x", "data": ["y0", "y1", "y2"]},
  {"name": "m_tready", "wave": "1........"}
], "head": {"text": "L = 3 shown; any constant L in 0..NTAPS+8 is fine"} }
```

---

## 3. What the testbench checks

Every output is compared **bit-exact** with `fir_model.fir()`, and every cycle is checked for
the valid/ready rules. All tests run for NTAPS = 16, 5 and 32 (9 tests × 3 = 27).

| Test | What it covers |
|---|---|
| `test_impulse_response` | impulse of `0x7FFF` → output = `h[0..N-1]` (within 1 LSB, bit-exact to the model); impulse of `0x8000` → exactly `-h`. Ascending, low-pass and random taps |
| `test_step_response` | ±0.5 and ± full-scale steps into a low-pass: settles at DC gain × step (the full-scale ones saturate) |
| `test_random_bitexact` | 1200 random full-scale samples × 4 coefficient sets: random (lots of saturation), small (no saturation), low-pass, sparse |
| `test_rounding` | exact ±0.5 LSB ties, ±1.5, near-ties; truncation / round-half-away / round-half-even all fail |
| `test_saturation` | all taps & samples at +max / -1.0 (largest possible accumulator), -1.0 × -1.0, worst-case sign pattern, values one LSB inside/outside the output range |
| `test_lowpass_tones` | a low tone + a high "interferer" through a numpy-designed low-pass; logs the attenuation |
| `test_latency_and_throughput` | `s_tready=1` every cycle, constant `L` within limits, isolated samples come out without a push, same `L` with input gaps |
| `test_backpressure` | random `s_tvalid` gaps + random `m_tready`, then long `m_tready=0` stretches |
| `test_reset` | reset mid-stream with results in flight (and with the output stalled): nothing old comes out, history restarts at zero |

On a mismatch the testbench prints the input, expected and actual values around the first bad
sample, the full-precision accumulator, and a guess at the cause (rounding, wrap-around,
valid/data misalignment…).

Try `fir_model` in a Python shell first (`cd tb && python -i fir_model.py`), and compute a few
outputs by hand.

---

## 4. Stretch goals (roughly in order of radar/DSP relevance)

* **Synthesize it** with Yosys (`synth_xilinx -top fir_filter`) or Vivado and count DSP48s,
  LUTs and registers. Does every multiplier land in a DSP? Did the M register get absorbed?
* **Symmetric (linear-phase) filter:** for `h[k] = h[N-1-k]`, pre-add the two samples that share
  a coefficient, then multiply: half the multipliers. DSP48s have a **pre-adder** for exactly
  this. (Add a `SYMMETRIC` parameter and your own test.)
* **Systolic or transposed version** of your design. Compare latency, Fmax and resource use.
* **Coefficients from a ROM:** replace the port with a `parameter string COEF_FILE` and
  `$readmemh`, and generate the hex file with `fir_model.lowpass()`.
* **Convergent rounding** (round half to even) and a measurement of the DC bias of truncation
  vs. round-half-up vs. convergent over a long random run.
* **Decimating FIR** (output every M-th sample, M× fewer multipliers with a polyphase
  structure), the workhorse of digital down-converters.
* **Complex (I/Q) filter / matched filter:** four real multipliers (or three with the Gauss
  trick) per tap. Build a pulse-compression matched filter for a Barker-13 code and watch the
  compressed peak.
* A skid buffer on the output so `s_tready` no longer depends combinationally on `m_tready`.

## 5. Interview questions you should be able to answer after this

1. What's Q1.15? What's the range, and what's the resolution? Why isn't +1.0 representable?
2. How many bits does a 16×16 signed product need? Which input pair needs the last bit?
3. How wide must the accumulator of a 64-tap filter be to never overflow? Can you use fewer
   bits if you know the coefficients? (Hint: Σ|h[k]|.)
4. Truncation vs. round-half-up vs. convergent rounding: what does each cost in hardware, and
   what bias does each leave?
5. Saturation vs. wrap-around: when is wrap-around actually fine? (Hint: intermediate
   results in two's complement sums, CIC filters.)
6. Direct form vs. transposed vs. systolic FIR: latency, fan-out, and how each maps onto DSP48
   slices and their cascade paths.
7. What's the latency of your design, and where does each cycle come from? What would you
   change to halve it?
8. Why shouldn't datapath registers in a DSP pipeline have a reset? What *must* be reset?
9. How does backpressure propagate through your pipeline? What would change if the input came
   straight from an ADC with no ready signal?
10. How does quantizing the coefficients to 16 bits change the frequency response? How would
    you check it in Python?
11. What is a matched filter, and why is radar pulse compression an FIR filter?

## 6. Suggested reading (links checked 2026-09-27)

* Wikipedia, *Q (number format)*: <https://en.wikipedia.org/wiki/Q_(number_format)> and
  *Finite impulse response*: <https://en.wikipedia.org/wiki/Finite_impulse_response>
* Steven W. Smith, *The Scientist and Engineer's Guide to DSP* (free online): ch. 15–16 on
  moving-average and windowed-sinc filters <https://www.dspguide.com/ch16.htm>, and
  *Fixed versus Floating Point* <https://www.dspguide.com/ch28/4.htm>
* Dan Gisselquist (ZipCPU), *Bit growth in FPGA arithmetic*
  <https://zipcpu.com/dsp/2017/07/21/bit-growth.html>, *Rounding numbers without adding a bias*
  <https://zipcpu.com/dsp/2017/07/22/rounding.html>, *A simple filter*
  <https://zipcpu.com/dsp/2017/08/19/simple-filter.html>, and *Building a high-speed FIR*
  <https://zipcpu.com/dsp/2017/09/15/fastfir.html> (read these **after** yours works)
* AMD/Xilinx, *7 Series DSP48E1 Slice User Guide* (UG479), especially the FIR filter chapter:
  <https://docs.amd.com/v/u/en-US/ug479_7Series_DSP48E1>, and *UltraScale DSP48E2* (UG579):
  <https://docs.amd.com/v/u/en-US/ug579-ultrascale-dsp>
* AMD/Xilinx, *FIR Compiler product guide* (PG149): what an industrial FIR core offers
  (coefficient reload, symmetry, decimation, rounding modes): <https://docs.amd.com/r/en-US/pg149-fir-compiler>
* Wikipedia, *Pulse compression* <https://en.wikipedia.org/wiki/Pulse_compression>,
  *Matched filter* <https://en.wikipedia.org/wiki/Matched_filter>,
  *Digital down converter* <https://en.wikipedia.org/wiki/Digital_down_converter>
* numpy, `np.convolve` (the golden model): <https://numpy.org/doc/stable/reference/generated/numpy.convolve.html>
* Books worth owning if radar DSP is the goal: M. A. Richards, *Fundamentals of Radar Signal
  Processing*; R. G. Lyons, *Understanding Digital Signal Processing*.
