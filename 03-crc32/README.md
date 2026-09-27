# 03 — Ethernet CRC-32 over a 64-bit AXI-Stream

**Goal:** build `crc32_axis`, which computes the IEEE 802.3 (Ethernet) CRC-32 of every frame on
a 64-bit AXI-Stream at **one beat (8 bytes) per clock**, including a partial last beat marked by
`tkeep`, and tells you whether the frame's FCS is correct.

This is where you meet the central trick of high-speed networking hardware: a bit-serial
algorithm (one bit per step) unrolled into wide **parallel** logic that does 64 bits per clock.
At 10 GbE with a 64-bit bus you're running at 156.25 MHz, one beat per cycle, no excuses.

Estimated effort: 6–10 hours.

| File | What it is |
|---|---|
| `rtl/crc32_axis.sv` | **Your code** |
| `tb/test_crc32_axis.py` | cocotb testbench; reference model is Python's `zlib.crc32` |
| `HINTS.md` | progressive hints |

```
make test | make lint | make wave
```

---

## 1. Background

### CRC in one paragraph

Treat the message bits as the coefficients of a huge polynomial over GF(2) (where addition is
XOR). Divide by a fixed **generator polynomial** and keep the remainder: that's the CRC. The
transmitter appends it (the Ethernet **FCS**, frame check sequence), and the receiver recomputes
it. CRC-32 catches all single, double and odd-count bit errors, and all burst errors up to 32
bits long. That's why it's in Ethernet, ZIP, PNG, SATA...

### Ethernet CRC-32 parameters (a.k.a. CRC-32/ISO-HDLC)

| Parameter | Value | Meaning |
|---|---|---|
| width | 32 | |
| poly | `0x04C11DB7` | x³²+x²⁶+x²³+x²²+x¹⁶+x¹²+x¹¹+x¹⁰+x⁸+x⁷+x⁵+x⁴+x²+x+1 |
| init | `0xFFFFFFFF` | register preset (so leading zero bytes still change the CRC) |
| refin / refout | true | bits are processed **LSB first** (Ethernet sends each byte LSB first) |
| xorout | `0xFFFFFFFF` | final inversion |
| check | `0xCBF43926` | CRC of ASCII `"123456789"` |
| residue | `0xDEBB20E3` | remainder of (message + correct FCS) *before* final XOR |

**Reflection:** because data enters LSB first, the usual implementation uses a **right-shifting**
register with the bit-reversed polynomial `0xEDB88320`. One bit step:

```
feedback = crc[0] ^ data_bit
crc      = (crc >> 1) ^ (feedback ? 32'hEDB88320 : 0)
```

Do this for bits 0..7 of byte 0, then byte 1, and so on. The FCS is `~crc`, sent least
significant byte first. This is exactly what `zlib.crc32` computes.

**Checking with the residue:** if you run the CRC over the *whole received frame including its
FCS*, the result (after the final XOR) is always the constant **`0x2144DF1C`**
(`= ~0xDEBB20E3`) when the frame is intact. So the hardware doesn't need to know where the
FCS starts. It just checks the running CRC against a constant at the end of the frame. That's
the output `m_crc_ok`.

### Parallel CRC

CRC is linear over GF(2): every bit of the next state is an XOR of some state bits and some
input bits. So "apply the one-bit step 64 times" is just a (big) XOR network, and you can write
it as a `for` loop inside a function and let synthesis flatten it. Tools like Forencich's
`lfsr.v` or OutputLogic's generator print the explicit XOR equations; for this project the loop
is fine and much more readable. (Stretch goal: look at the LUT depth and the timing.)

**The tkeep problem:** the last beat can hold 1–8 valid bytes. You need the CRC after exactly
`n` bytes. One clean approach is to compute the state after 1, 2, …, 8 bytes and select with
`tkeep`. Another is to shift the partial beat so the valid bytes go last and then use a
correction. The first approach is easier; the second saves area.

---

## 2. Specification

### Ports

| Port | Dir | Width | Description |
|---|---|---|---|
| `clk` | in | 1 | clock |
| `rst` | in | 1 | synchronous, active-high |
| `s_tdata` | in | 64 | frame bytes, **byte 0 in `s_tdata[7:0]`**, byte 7 in `[63:56]` |
| `s_tkeep` | in | 8 | byte enables: `8'hFF` except on the last beat, where it's contiguous from bit 0 (`8'h01`, `8'h03`, … `8'hFF`) |
| `s_tvalid` | in | 1 | beat valid |
| `s_tready` | out | 1 | **must be 1 every cycle after reset** (full line rate, never stall) |
| `s_tlast` | in | 1 | last beat of the frame |
| `m_crc_valid` | out | 1 | 1-cycle pulse, one per frame |
| `m_crc` | out | 32 | CRC-32 of all bytes of the frame, equal to `zlib.crc32(frame)` |
| `m_crc_ok` | out | 1 | `m_crc == 32'h2144DF1C`, i.e. the frame ends with a correct FCS |

### Behavior

1. A beat is consumed when `s_tvalid && s_tready` at a rising edge. `s_tvalid` may drop between
   beats (bubbles). While `s_tvalid=0`, all other inputs are **junk**, and so are the byte lanes
   that `s_tkeep` marks invalid. Ignore them.
2. Frames are ≥ 1 byte. The testbench covers every length from 1 to 80, plus up to 1518.
3. For each frame, `m_crc_valid` pulses once. If the tlast beat is consumed at edge *E*, the
   pulse is visible anywhere from the cycle right after *E* (a registered output) to 3 cycles
   later. Results come out **in order**.
4. Back-to-back single-beat frames (tlast every cycle) must produce one result per cycle.
5. Reset in the middle of a frame discards that frame (no result) and the next frame starts
   from a clean CRC state.

```
clk        _/‾\_/‾\_/‾\_/‾\_/‾\_/‾\_/‾\_
s_tvalid   _/‾‾‾‾‾‾‾‾‾‾‾\___/‾‾‾\_______
s_tdata    -< B0 >< B1 >-----< B2 >------     frame = B0 (8 bytes) + B1 (8 bytes) + B2 (3 bytes)
s_tkeep    -< FF >< FF >-----< 07 >------
s_tlast    _____________\___/‾‾‾\_______
m_crc_valid___________________/‾‾‾\_____     (latency 0 shown: registered output)
m_crc      -------------------< C >-----
```

```json
{ "signal": [
  {"name": "clk", "wave": "p........"},
  {"name": "s_tvalid", "wave": "01.010..."},
  {"name": "s_tdata", "wave": "x==x=x...", "data": ["B0", "B1", "B2"]},
  {"name": "s_tkeep", "wave": "x=.x=x...", "data": ["FF", "07"]},
  {"name": "s_tlast", "wave": "0...10..."},
  {"name": "m_crc_valid", "wave": "0....10.."},
  {"name": "m_crc", "wave": "x....=x..", "data": ["crc"]},
  {"name": "m_crc_ok", "wave": "x....=x..", "data": ["ok"]}
], "head": {"text": "19-byte frame with a bubble; result one cycle after the tlast edge"} }
```

---

## 3. What the testbench checks

`test_check_value` (`"123456789"` → `0xCBF43926`, plus 1-byte frames), `test_every_length`
(1..80 bytes), `test_good_fcs` (random 60–404-byte frames plus a 1518-byte one with a correct
FCS → `m_crc_ok=1`), `test_bad_fcs` (1–3 flipped bits → `m_crc_ok=0`),
`test_back_to_back_single_beat`, `test_bubbles` (50 % random `s_tvalid` gaps with junk on the
bus), `test_reset_mid_frame`. Every cycle it also checks `s_tready=1`. Every result is compared
with `zlib.crc32`, including the latency window.

---

## 4. Stretch goals

* **Timing:** synthesize with Yosys (`synth -top crc32_axis`, or `synth_xilinx`) and report
  the logic depth. Then pipeline the tkeep selection, or use the "CRC of zeros"/shift trick to
  cut area. Compare.
* Generate the explicit XOR equations with a small Python script (a matrix over GF(2)) and
  compare against the loop version.
* 512-bit (64-byte) bus version: what changes, what gets expensive?
* **FCS stripping + drop:** a wrapper that forwards the frame without its last 4 bytes and
  sets `tuser=1` on the last beat when the FCS is bad. Tricky, because the last 4 bytes can
  straddle two beats.
* An FCS *generator*: append the CRC to an outgoing frame.

## 5. Interview questions you should be able to answer after this

1. What does a CRC detect that a simple checksum (like the IP header checksum) doesn't?
2. Explain init = `0xFFFFFFFF` and xorout = `0xFFFFFFFF`. What would go wrong with init = 0?
3. What does "reflected" mean, and why does Ethernet use a reflected CRC?
4. Why does a correct frame + FCS always give the same residue? How does the hardware use that?
5. How do you turn a bit-serial CRC into a 64-bit-per-clock one? Why is that allowed?
6. How did you handle a last beat with only 3 valid bytes? What's the cost of your approach?
7. What's the critical path in your design, and how would you pipeline it for 322 MHz
   (25 GbE, 64-bit)?
8. The CRC check result arrives after the whole frame has already been forwarded. What do
   cut-through NICs and switches do about bad frames? (Look up how `tuser` / "bad frame"
   markers are used.)
9. What is AXI-Stream `tkeep` vs `tstrb`?
10. Why does HFT care about CRC at all when the exchange feed also has sequence numbers?

## 6. Suggested reading (links checked 2026-09-27)

* Ross Williams, *A Painless Guide to CRC Error Detection Algorithms*, the best intro ever
  written: <http://www.ross.net/crc/download/crc_v3.txt>
* Greg Cook's *CRC RevEng catalogue* (CRC-32/ISO-HDLC entry: parameters, check, residue):
  <https://reveng.sourceforge.io/crc-catalogue/17plus.htm>
* Wikipedia, *Cyclic redundancy check* and *Computation of cyclic redundancy checks*:
  <https://en.wikipedia.org/wiki/Cyclic_redundancy_check>,
  <https://en.wikipedia.org/wiki/Computation_of_cyclic_redundancy_checks>
* Wikipedia, *Ethernet frame* (FCS section): <https://en.wikipedia.org/wiki/Ethernet_frame>
* Alex Forencich, `lfsr.v` in *verilog-ethernet*, a parameterized parallel LFSR/CRC generator
  used in real 10G/25G MACs (read it **after** you've done yours):
  <https://github.com/alexforencich/verilog-ethernet/blob/master/rtl/lfsr.v>
* Michael Büsch, *crcgen*, which generates parallel CRC Verilog/VHDL/C:
  <https://bues.ch/cms/hacking/crcgen>
* ARM, *AMBA AXI-Stream Protocol Specification* (IHI 0051), for tkeep/tlast semantics:
  <https://developer.arm.com/documentation/ihi0051/b>
