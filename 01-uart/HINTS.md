# 01-uart hints

Open one level at a time. Spend at least 30 minutes stuck before opening the next.
Nothing here is a full solution, and if you write down which hints you used in the progress
checklist, you'll always be able to say exactly what you did yourself.

<details><summary><b>Level 1: structure</b></summary>

* TX: you need (a) a counter that counts `0 .. CLKS_PER_BIT-1`, (b) something that knows which
  of the 10 bits you're on, and (c) the byte you captured at the handshake.
* A 10-bit shift register `{stop=1, data[7:0], start=0}` that shifts right once per bit period,
  with `tx` driven by bit 0, is a compact alternative to an explicit FSM.
* RX: an FSM with states like `IDLE → START → DATA → STOP (→ WAIT_IDLE)` plus the same kind of
  counter.
* Pick counter widths with `$clog2(CLKS_PER_BIT)` (or `+1` if you count up to `CLKS_PER_BIT`).
</details>

<details><summary><b>Level 2: TX details</b></summary>

* When should `s_ready` be 1? Simplest: whenever you're idle. That gives you one idle cycle
  between frames, which the spec allows. For zero idle cycles, you can also be ready in the very
  last cycle of the stop bit.
* Drive `tx` from a flip-flop, not from combinational logic. A glitchy `tx` would be decoded as
  bits by the testbench (and by a real receiver).
* On `rst`, force `tx` to 1 and go idle. The test checks this in the very next cycle.
</details>

<details><summary><b>Level 3: RX details</b></summary>

* Falling-edge detection: keep the previous synchronized value and look for `prev=1, now=0`.
* After the edge, count `CLKS_PER_BIT/2` cycles and look at the line. Still 0? Real start bit.
  Then sample every `CLKS_PER_BIT` cycles from there.
* Your synchronizer adds ~2 cycles of delay. If the edge detector and the sampler both look at
  the same synchronized signal, that delay cancels out.
* The shift direction for LSB-first: new bit enters at the top, shift right:
  `data <= {bit, data[7:1]}`.
* After a framing error, go to a state that waits for the synchronized `rx` to be 1 before
  returning to idle.
</details>

<details><summary><b>Level 4: debugging failing tests</b></summary>

* `make wave TOP=uart_rx` and open the FST. Add `rx`, your synchronized rx, state, counter,
  bit index. Find the first sample point that's in the wrong place.
* "bit is not a constant level for exactly N clocks": your bit period is off by one. Check
  whether your counter compare is `== CLKS_PER_BIT-1` or `== CLKS_PER_BIT`, and whether the
  start bit gets an extra cycle when you leave IDLE.
* "byte appearing twice": `m_valid` is high for 2+ cycles. Make it a default-0 register
  that's only set in one cycle.
* Fails only on `test_slow_transmitter`: you sample too early in the bit.
  Fails only on `test_fast_transmitter`: too late, or you aren't ready for the next start edge
  soon enough after the stop bit.
</details>
