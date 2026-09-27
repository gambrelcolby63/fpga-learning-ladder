# 05-fir-filter hints

<details><summary><b>Level 1: do the math in Python first, then build it in three pieces</b></summary>

Open `tb/fir_model.py` in a Python shell and compute a few outputs by hand: an impulse, a step
of `0x4000`, and `(-32768) * (-32768)` through a single tap. When you can predict every number
(including why an impulse of `0x7FFF` gives *almost* `h`), you understand the spec.

Then build and check one piece at a time (`make wave` is your friend):

1. **Delay line + products + sum, no flow control yet.** Tie `s_tready = 1`, ignore `m_tready`,
   and get `test_impulse_response` passing. It tells you whether the tap order is right
   (`h[0]` × newest sample).
2. **Round and saturate.** Now `test_random_bitexact`, `test_rounding` and `test_saturation`.
3. **Pipelining + valid bits + backpressure.** Then the remaining tests.

Unpack the coefficients into an array first, so the rest of the code reads like the math:
`logic signed [15:0] h [NTAPS];` and in an `always_comb` loop, `h[k] = $signed(coefs[16*k +: 16]);`
</details>

<details><summary><b>Level 2: widths and signedness (where most of the bugs live)</b></summary>

* **Every** operand in a multiply or add must be `signed`. In SystemVerilog one unsigned
  operand makes the whole expression unsigned, and part-selects like `coefs[16*k +: 16]` are
  always unsigned, so use `$signed(...)`. Symptom: results are fine for positive values and
  garbage for negative ones.
* A signed×signed product assigned to a `logic signed [31:0]` is computed at 32 bits. Don't
  make it `[30:0]` "because inputs are below 1.0": `-1.0 × -1.0` needs bit 31.
* When you widen a signed value, it must be **sign-extended**: `ACC_W'(p)` on a signed `p`
  does it, while `{4'b0, p}` does not.
* Rounding and saturation, step by step: `r = acc + (1 <<< 14)` (at full accumulator width),
  then `s = r >>> 15` (`>>>` on a **signed** value is an arithmetic shift; `>>` is not), then
  clamp `s` to `[-32768, 32767]`. The clamp can be two compares, or "are all the bits above
  bit 15 copies of bit 15?".
* Make `m_tdata` a register and assign it the clamped value. Never output `s[15:0]` without
  the clamp.
</details>

<details><summary><b>Level 3: pipelining and flow control</b></summary>

* A simple scheme that maps well onto DSP clock-enable pins: one **advance** signal for the
  whole pipeline, e.g. "the output register is empty or is being emptied this cycle". Every
  pipeline register updates only when it's high. `s_tready` is that same signal.
* Carry a **valid bit** alongside each pipeline stage (a small shift register with the same
  enable). The output's `m_tvalid` is the last valid bit. Bubbles then flow through harmlessly.
* The **delay line** is different: it moves only on an accepted sample (`s_tvalid && s_tready`),
  never on an idle cycle. Pipeline registers move on every advance, even for bubbles.
* Adder tree for any `NTAPS`: pad the products up to the next power of two with zeros, then
  add pairs level by level, one register per level. Everything stays aligned because all
  leaves are the same depth. (A transposed-form chain is a good alternative: its latency
  doesn't depend on `NTAPS`.)
* Reset only what matters: the valid bits, `m_tvalid`, and the delay line (it must restart at
  zero). Products and partial sums don't need a reset. Their valid bit says whether they
  mean anything, and on real hardware skipping the reset helps the tools pack them into DSP slices.
* Count your latency on paper, register by register, and write it in a comment. The test log
  prints the measured `L`. Check that they agree.
</details>

<details><summary><b>Level 4: debugging</b></summary>

The testbench prints a table around the first wrong sample and a guess at the cause. Common ones:

* **Off by exactly 1 LSB:** rounding. Missing `+2^14`, adding it at the wrong bit position,
  `>>` instead of `>>>`, or rounding after saturating.
* **`test_rounding` fails only on negative ties** (`-0.5` gave `-1`): you round half *away from
  zero*. The spec wants half *up*: plain add-then-floor handles negatives correctly.
* **Big values have the wrong sign:** missing or wrong saturation (wrap-around), accumulator too
  narrow (`test_saturation` with all -1.0), or a 31-bit product.
* **Negative results wildly wrong:** an unsigned operand somewhere, or zero-extension instead of
  sign-extension.
* **Impulse response shifted, reversed, or missing the last tap:** delay-line indexing (`h[0]`
  goes with the *newest* sample) or a loop bound off by one.
* **Output is the previous or next sample's result:** the valid bit and the data went through a
  different number of registers.
* **Fails only with gaps / in `test_backpressure`:** the delay line shifts on idle cycles, or a
  register ignores the advance signal (then `m_tdata` changes while stalled).
* **"isolated sample: no output":** your pipeline only moves when a new sample arrives. It must
  keep moving (and draining) on its own.
* **`test_reset` fails:** the delay line or the valid bits aren't cleared, so old samples leak
  out after reset.
</details>
