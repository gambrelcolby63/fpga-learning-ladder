# 04 — Ethernet II / IPv4 / UDP header parser with payload realignment

**Goal:** build `eth_udp_parser`. It takes raw Ethernet frames on a 64-bit AXI-Stream, extracts
the MAC, IP and UDP header fields into a header record, forwards the **UDP payload** as a new
AXI-Stream **realigned so payload byte 0 is in lane 0**, and silently drops everything that
isn't IPv4/UDP.

This is the stepping stone to a market-data feed handler. Nasdaq TotalView-ITCH is delivered
as **MoldUDP64** packets over UDP multicast, so the first thing an HFT FPGA does with a frame is
exactly this: strip Ethernet/IP/UDP, find the payload, and hand it to the protocol parser, with
fields straddling 8-byte beat boundaries, in a handful of cycles.

Estimated effort: 12–25 hours. This is the hardest rung. Budget time for waveform debugging.

| File | What it is |
|---|---|
| `rtl/eth_udp_parser.sv` | **Your code** |
| `tb/test_eth_udp_parser.py` | cocotb testbench |
| `tb/eth_model.py` | frame builder + reference parser (`parse()`) |
| `HINTS.md` | progressive hints |

```
make test | make lint | make wave
```

---

## 1. Background

### The headers (all multi-byte fields are big-endian, "network byte order")

```
Ethernet II (14 bytes)      IPv4 (IHL*4 bytes, IHL = 5..15)            UDP (8 bytes)
 0  dst MAC   (6)            14  version(4b) | IHL(4b)                  +0 src port  (2)
 6  src MAC   (6)            15  DSCP/ECN                               +2 dst port  (2)
12  ethertype (2) =0x0800    16  total length (2)                       +4 length    (2) = 8 + payload
                             18  identification (2)                     +6 checksum  (2)
                             20  flags | fragment offset (2)
                             22  TTL
                             23  protocol  = 17 for UDP
                             24  header checksum (2)
                             26  source IP (4)
                             30  destination IP (4)
                             34  options (IHL-5)*4 bytes ...  then UDP at 14 + 4*IHL
```

### Where things land on a 64-bit bus (IHL = 5)

Byte *n* of the frame is in beat `n / 8`, lane `n % 8` (`s_tdata[8*lane +: 8]`).

| beat | lane 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| 0 | dst0 | dst1 | dst2 | dst3 | dst4 | dst5 | src0 | src1 |
| 1 | src2 | src3 | src4 | src5 | **type_hi** | **type_lo** | **ver/IHL** | dscp |
| 2 | totlen | totlen | id | id | flags | frag | ttl | **proto** |
| 3 | csum | csum | **sip0** | sip1 | sip2 | sip3 | **dip0** | dip1 |
| 4 | dip2 | dip3 | **sport** | sport | dport | dport | **ulen** | ulen |
| 5 | ucsum | ucsum | **pay0** | pay1 | pay2 | pay3 | pay4 | pay5 |
| 6 | pay6 | … | | | | | | pay13 |

With IHL = 6 (4 bytes of options), UDP starts at byte 38 (beat 4, lane 6), the UDP length lands in
beat 5 lanes 2–3, and the payload starts at beat 5 lane 6. Note that the source MAC, the
destination IP, and (with options) the UDP ports and length **straddle beat boundaries**. That's
the core difficulty: a field's bytes arrive in different clock cycles, and in some cases the
UDP length you need to find the payload's end arrives in the **same beat** as the first payload
byte.

### Realignment

With IHL = 5 payload byte 0 sits in lane 2. Each 4-byte option word moves it by 4 lanes, so it's
lane 2 for odd IHL and lane 6 for even IHL. Downstream logic (an ITCH parser) wants it in lane 0. So each output beat is built from the
top part of one input beat and the bottom part of the next:

```
input  beat 5: [ucsum ucsum p0 p1 p2 p3 p4 p5]      beat 6: [p6 p7 p8 p9 p10 p11 p12 p13]
output beat 0: [p0 p1 p2 p3 p4 p5 p6 p7]            output beat 1: [p8 ... ]
```

The end of the payload is set by the **UDP length**, not by `tlast`. Short frames are padded
to 60 bytes, and the padding must **not** be forwarded. At the end you may need one extra output
beat to flush the bytes left in your holding register.

### Why drop, and what "drop" means here

A feed handler only wants UDP. Everything else (ARP, IPv6, TCP, VLAN-tagged traffic in this
base spec) is consumed from the input and produces **no output at all**.

---

## 2. Specification

### Ports

| Port | Dir | Width | Description |
|---|---|---|---|
| `clk`, `rst` | in | 1 | clock; synchronous active-high reset |
| `s_tdata` | in | 64 | frame bytes, byte 0 in `[7:0]`. Frames start at the destination MAC; FCS already removed |
| `s_tkeep` | in | 8 | all ones except the last beat (contiguous from bit 0) |
| `s_tvalid`, `s_tready`, `s_tlast` | | 1 | AXI-Stream handshake / end of frame |
| `m_hdr_valid` | out | 1 | a header record is available |
| `m_hdr_ready` | in | 1 | downstream accepts the header record |
| `m_dst_mac`, `m_src_mac` | out | 48 | first byte on the wire = bits `[47:40]` |
| `m_ethertype` | out | 16 | always `16'h0800` for forwarded frames |
| `m_ip_src`, `m_ip_dst` | out | 32 | first byte = `[31:24]` (so 10.0.0.1 = `32'h0A000001`) |
| `m_udp_src_port`, `m_udp_dst_port` | out | 16 | |
| `m_udp_len` | out | 16 | UDP length field (8 + payload length) |
| `m_tdata`, `m_tkeep`, `m_tvalid`, `m_tready`, `m_tlast` | | 64/8/1/1/1 | payload stream |

### Forwarding rules

A frame is **forwarded** iff all of:

* ethertype == `0x0800`
* IP version == 4 and IHL ≥ 5 (IHL 5..15 must work; options are skipped, not parsed)
* IP protocol == 17
* UDP length ≥ 8
* the frame is long enough to contain the complete UDP header (`14 + 4*IHL + 8` bytes)

Everything else is **dropped**: consumed from the input with no header record and no payload.
IP checksum, IP total length, fragmentation and UDP checksum are **not** checked in the base
spec (see stretch goals).

### Outputs for a forwarded frame

1. Exactly **one header record** (handshake `m_hdr_valid`/`m_hdr_ready`). While `m_hdr_valid=1`
   and `m_hdr_ready=0` the fields must not change and `m_hdr_valid` must stay 1.
2. The payload = frame bytes `[14+4*IHL+8, 14+4*IHL+UDP_length)`, as one AXI-Stream packet:
   payload byte 0 in `m_tdata[7:0]` of the first beat; every beat full (`m_tkeep=8'hFF`)
   except the last; `m_tlast` on the last; `m_tkeep` contiguous from bit 0. Standard AXI-Stream
   rules apply: once `m_tvalid=1`, the beat must stay put until `m_tready=1`.
3. **Zero-length payload** (UDP length = 8): header record only, no payload beats.
4. **Padding / trailing junk** after the UDP payload: not forwarded.
5. **Truncated frame** (the frame ends before UDP length says it should, but after the UDP
   header): header record as usual; the payload stream ends with `m_tlast` at the last byte
   that actually arrived. If no payload byte arrived, there are no payload beats.
6. Header records and payload packets are two **independent in-order streams**. The testbench
   drives `m_hdr_ready` and `m_tready` with independent random patterns and never makes one
   depend on the other. You may emit the header before, during, or after the payload, and you
   may stall the input (`s_tready=0`) whenever an output can't accept.
7. Throughput: with no backpressure, at most **4 input stall cycles per frame** on average
   (checked). Zero stalls, i.e. full line rate, is a stretch goal that the reference design
   achieves.

### Timing (IHL = 5, 20-byte payload, no backpressure; latency is up to you)

```
beat:       0      1      2      3      4       5          6          7
s_tdata  <dst..><src..><ip..><ip..><dip|udp><ucs|p0-p5><p6-p13><p14-p19>
s_tlast  ____________________________________________________________/‾‾‾‾‾‾\
m_hdr_valid                               __/‾‾‾\_____ (fields known after beat 4)
m_tdata                                              <p0-p7><p8-p15><p16-p19>
m_tkeep                                              < FF  >< FF   >< 0F    >
m_tlast                                              _______________/‾‾‾‾‾‾\
```

```json
{ "signal": [
  {"name": "clk", "wave": "p..........."},
  {"name": "s_tvalid", "wave": "01.......0.."},
  {"name": "s_tdata", "wave": "x========x..", "data": ["dst,src", "src,type,ver", "ip", "ip,sip", "dip,udp", "ucs,p0-5", "p6-13", "p14-19"]},
  {"name": "s_tlast", "wave": "0.......10.."},
  {"name": "m_hdr_valid", "wave": "0.....10...."},
  {"name": "m_tvalid", "wave": "0......1..0."},
  {"name": "m_tdata", "wave": "x......===x.", "data": ["p0-7", "p8-15", "p16-19"]},
  {"name": "m_tkeep", "wave": "x......=.=x.", "data": ["FF", "0F"]},
  {"name": "m_tlast", "wave": "0........10."}
], "head": {"text": "one possible schedule; any latency is fine"} }
```

---

## 3. What the testbench checks

Reference model: `eth_model.parse(frame)` works on raw bytes and returns the expected header and
payload (or `None` for drop). The scoreboard compares the header stream and the payload stream
separately, in order, plus AXI-Stream protocol rules on both outputs.

| Test | What it covers |
|---|---|
| `test_single_udp` | smoke test |
| `test_payload_lengths` | payload 0..40 bytes × IHL 5 and 6: every realignment/flush case |
| `test_ip_options` | IHL 5..15 |
| `test_padding_and_trailer` | tiny payloads padded to 60 bytes + junk after the datagram |
| `test_drop_non_udp` | ARP, IPv6, VLAN, TCP, ICMP, wrong IP version, IHL<5, UDP length<8, valid UDP bytes behind a non-IPv4 ethertype, runts; interleaved with good frames |
| `test_truncated_frames` | UDP length longer than the frame |
| `test_random_mix_backpressure` | everything, with random input bubbles and random `m_tready` / `m_hdr_ready` |
| `test_header_backpressure` | `m_hdr_ready` stuck low for 600 cycles |
| `test_throughput` | back-to-back frames, stall-cycle budget (logs your stall count) |
| `test_reset_mid_frame` | reset while half-way through a frame |

---

## 4. Stretch goals (roughly in order of HFT relevance)

* **Line rate:** zero input stall cycles with no backpressure (`test_throughput` prints your number).
* **Latency:** measure first-input-byte → first-payload-byte latency in cycles; get it down.
  Then think about **cut-through**: start forwarding payload before the frame's FCS is checked.
* **MoldUDP64 + ITCH:** add a stage after this one that parses the MoldUDP64 header (session,
  sequence number, message count) and splits the payload into ITCH messages using the 2-byte
  length prefixes. That's the natural project 05.
* 802.1Q VLAN support (one tag): the IPv4 header moves by 4 bytes.
* Destination filtering: only forward frames to a configured multicast MAC / IP / UDP port.
* Check the IPv4 header checksum (RFC 1071) and drop bad headers. Drop IP fragments.
* Combine with project 03: drop frames with a bad FCS (needs buffering, or a `tuser`
  "bad frame" flag on the last payload beat).

## 5. Interview questions you should be able to answer after this

1. Walk through the byte offsets of Ethernet/IPv4/UDP. Where does the payload start with and
   without IP options?
2. Which header fields straddle beat boundaries on a 64-bit bus? How does your design capture them?
3. Why can't you use `tlast` to find the end of the UDP payload?
4. How does your realignment work? How many bytes can be "in flight" inside your parser?
5. What's the latency of your parser in cycles? What limits it? What would you change for
   minimum latency?
6. How does backpressure propagate through your design? Can it deadlock? Why not?
7. What's an ethertype, and what changes with a VLAN tag?
8. How does multicast work at the Ethernet and IP layers? Why do exchanges use UDP multicast
   for market data?
9. What is MoldUDP64, and how would you detect a gap (lost packet)?
10. At 10 GbE with a 64-bit bus, how many clock cycles does a minimum-size frame take? What's
    the clock frequency?

## 6. Suggested reading (links checked 2026-09-27)

* Wikipedia, *Ethernet frame*: <https://en.wikipedia.org/wiki/Ethernet_frame>
* RFC 791, *Internet Protocol* (IPv4 header, section 3.1): <https://www.rfc-editor.org/rfc/rfc791>
* RFC 768, *User Datagram Protocol*: <https://www.rfc-editor.org/rfc/rfc768>
* RFC 1071, *Computing the Internet Checksum* (for the stretch goal): <https://www.rfc-editor.org/rfc/rfc1071>
* Wikipedia, *IEEE 802.1Q* (VLAN tags): <https://en.wikipedia.org/wiki/IEEE_802.1Q>
* Nasdaq, *MoldUDP64 Protocol Specification*:
  <https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf>
* Nasdaq, *TotalView-ITCH 5.0 Specification*:
  <https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf>
* Alex Forencich, *verilog-ethernet* (`eth_axis_rx`, `ip_eth_rx`, `udp_ip_rx` do exactly this job
  with AXI-Stream; read them **after** yours works): <https://github.com/alexforencich/verilog-ethernet>
  and its successor *taxi*: <https://github.com/fpganinja/taxi>
* ARM, *AMBA AXI-Stream Protocol Specification*: <https://developer.arm.com/documentation/ihi0051/b>
