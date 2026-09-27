# 03-crc32 hints

<details><summary><b>Level 1: get the math right first, in Python</b></summary>

Before writing RTL, write a 10-line Python bit-by-bit CRC using the right-shifting reflected
algorithm from the README and check it against `zlib.crc32(b"123456789") == 0xCBF43926`.
When your Python matches, you understand the algorithm. The RTL is a translation.
</details>

<details><summary><b>Level 2: one byte, then eight</b></summary>

* Write a SystemVerilog `function automatic logic [31:0] crc_byte(logic [31:0] c, logic [7:0] d)`
  that loops over the 8 bits. Loops in functions are fine: synthesis unrolls them into XORs.
* Chain it: state after byte 0, after byte 1, … after byte 7, in an `always_comb` with an array
  `logic [31:0] after [9]` (after[0] = current state).
* Byte `i` of the beat is `s_tdata[8*i +: 8]`.
</details>

<details><summary><b>Level 3: frame handling</b></summary>

* Keep a 32-bit `state` register. On a non-last beat: `state <= after[8]`.
* On the last beat, pick `after[n]` where `n` = number of valid bytes (tkeep is contiguous, so
  `n` = position of the highest set bit + 1), output `~after[n]`, and re-initialize `state`
  to `32'hFFFF_FFFF` for the next frame.
* Only update anything when `s_tvalid` is 1.
</details>

<details><summary><b>Level 4: debugging</b></summary>

* Every CRC wrong, even for `"123456789"`: check bit order (LSB first), the reflected
  polynomial, init, and the final inversion, in that order.
* Only multi-beat frames wrong: state isn't carried between beats, or isn't reset after tlast.
* Only some lengths wrong: tkeep selection off by one.
* Wrong only in `test_bubbles`: you update the state when `s_tvalid=0`.
* `m_crc_ok` wrong but `m_crc` right: compare against `32'h2144DF1C` *after* the final XOR.
</details>
