# 04-eth-parser hints

<details><summary><b>Level 1: split the problem</b></summary>

Build it in three pieces and get each one working (with `make wave`) before the next:

1. **Beat counter / byte offset:** a register holding the frame byte offset of `s_tdata[7:0]`
   (0, 8, 16, …), reset to 0 after each `tlast`.
2. **Header capture:** for each lane, compute its absolute byte offset and, if it matches a field
   byte, store it into that field's register. Now straddling fields are a non-issue: each byte is
   captured whenever it shows up.
3. **Payload path:** decide per lane whether the byte is payload, then realign.

Get `test_single_udp` passing on headers first. The payload check fails until part 3 works,
but you can watch the header stream in the waveform.
</details>

<details><summary><b>Level 2: header capture details</b></summary>

* The UDP header offset is `14 + 4*IHL`. IHL arrives in beat 1 and UDP starts in beat 4 at the
  earliest, so a registered IHL is available in time.
* The UDP length, though, can arrive in the *same* beat as the first payload byte (IHL = 6, 8, …).
  A neat trick: compute a combinational "next" version of each header register (register
  value, overwritten by any bytes in the current beat) and use the *next* values for decisions
  in the current beat.
* The forward/drop decision is fully known when the last UDP header byte arrives. Emit the
  header record then.
</details>

<details><summary><b>Level 3: realignment</b></summary>

Two common architectures:

* **Fixed-shift realigner:** the payload offset mod 8 is known per frame (2 or 6 here). Keep the
  previous input beat and output `{cur[lo bytes], prev[hi bytes]}`. Needs special cases for the
  first beat, the last beat, and the extra flush beat.
* **Byte buffer:** a 16-byte holding register plus a byte count. Each accepted beat *appends*
  its payload bytes (shifted down to remove the leading non-payload lanes); whenever ≥ 8 bytes
  are held, or the payload has ended, pop one output beat. This handles every alignment and the
  UDP-length truncation uniformly, at the cost of wider muxes.

Either way, when you need to stall, stall the whole input (`s_tready=0`). Never drop a
beat you've already accepted.
</details>

<details><summary><b>Level 4: debugging</b></summary>

* Header mismatch: print the expected vs got fields, find which field, and check its byte
  offsets. For MAC/IP fields, the first byte on the wire goes in the **most** significant byte.
* Payload off by 2 or 6 bytes: wrong payload start offset (did you add 8 for the UDP header?).
* Payload too long by the padding: you ended on `tlast` instead of the UDP length.
* Payload wrong only with IHL even/odd: see "UDP length in the same beat" in Level 2.
* Protocol errors "beat changed while stalled": your output register updates while
  `m_tvalid && !m_tready`.
* `test_drop_non_udp` fails: check which rule you're missing. The test names each frame kind in
  `eth_model.OTHER_KINDS`.
</details>
