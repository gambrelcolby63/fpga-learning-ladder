"""cocotb testbench for eth_udp_parser (see ../README.md for the spec it checks).

Header records and payload frames are checked as two independent in-order streams against the
Python reference model (eth_model.parse). Frames that must be dropped produce neither.
"""
from __future__ import annotations

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

from eth_model import OTHER_KINDS, Hdr, build_other, build_udp, parse
from ladder_tb import AxisSink, AxisSource, resolvable, scale, u

HDR_FIELDS = ["m_dst_mac", "m_src_mac", "m_ethertype", "m_ip_src", "m_ip_dst",
              "m_udp_src_port", "m_udp_dst_port", "m_udp_len"]


def rbytes(n: int) -> bytes:
    return bytes(random.getrandbits(8) for _ in range(n))


class Tb:
    def __init__(self, dut, p_idle=0.0, p_ready=1.0, p_hdr_ready=1.0):
        self.dut = dut
        dut.rst.value = 1
        dut.m_hdr_ready.value = 0
        Clock(dut.clk, 4, unit="ns").start()
        self.src = AxisSource(dut.clk, dut, "s_", 8, p_idle)
        self.sink = AxisSink(dut.clk, dut, "m_", 8, p_ready)
        self.p_hdr_ready = p_hdr_ready
        self.hdrs: list[Hdr] = []
        self.errors: list[str] = []
        self.frames: list[bytes] = []
        self.cycles = 0
        self.stall_cycles = 0
        cocotb.start_soon(self._hdr_monitor())

    async def _hdr_monitor(self):
        dut = self.dut
        stalled = None
        while True:
            await RisingEdge(dut.clk)
            r = random.random() < self.p_hdr_ready
            dut.m_hdr_ready.value = int(r)
            await ReadOnly()
            self.cycles += 1
            if u(dut.rst) or not resolvable(dut.m_hdr_valid):
                stalled = None
                continue
            if u(dut.s_tvalid) and not u(dut.s_tready):
                self.stall_cycles += 1
            v = u(dut.m_hdr_valid)
            if stalled is not None:
                now = Hdr(*[u(getattr(dut, f)) for f in HDR_FIELDS])
                if not v:
                    self.errors.append("m_hdr_valid dropped before the header was accepted")
                elif now != stalled:
                    self.errors.append(f"header fields changed while m_hdr_valid=1 and m_hdr_ready=0: {stalled} -> {now}")
            stalled = None
            if v:
                h = Hdr(*[u(getattr(dut, f)) for f in HDR_FIELDS])
                if r:
                    self.hdrs.append(h)
                else:
                    stalled = h

    async def reset(self):
        self.dut.rst.value = 1
        for _ in range(4):
            await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        await RisingEdge(self.dut.clk)

    def send(self, frames: list[bytes]):
        for f in frames:
            self.frames.append(f)
            self.src.send(f)

    async def finish(self, quiet_cycles: int = 60, max_cycles: int = 400000):
        n = quiet = 0
        last = (-1, -1)
        while quiet < quiet_cycles:
            await RisingEdge(self.dut.clk)
            n += 1
            assert n < max_cycles, "timeout: DUT stopped making progress"
            state = (len(self.hdrs), len(self.sink.frames))
            busy = bool(self.src.queue) or state != last
            last = state
            quiet = 0 if busy else quiet + 1

    def check(self):
        exp = [parse(f) for f in self.frames]
        exp_hdrs = [e[0] for e in exp if e is not None]
        exp_pay = [e[1] for e in exp if e is not None and len(e[1]) > 0]
        errs = self.errors + self.sink.errors
        assert not errs, "protocol errors:\n  " + "\n  ".join(errs[:10])
        assert not self.src.queue, f"{len(self.src.queue)} input beats were never accepted (s_tready stuck at 0?)"
        for i, (e, g) in enumerate(zip(exp_hdrs, self.hdrs)):
            assert e == g, f"header #{i} mismatch\n  expected {e}\n  got      {g}"
        assert len(self.hdrs) == len(exp_hdrs), (
            f"{len(exp_hdrs)} headers expected (UDP frames), got {len(self.hdrs)}")
        for i, (e, g) in enumerate(zip(exp_pay, self.sink.frames)):
            assert e == g, (f"payload #{i} mismatch ({len(e)} bytes expected, {len(g)} got)\n"
                            f"  expected {e[:24].hex()}...\n  got      {g[:24].hex()}...")
        assert len(self.sink.frames) == len(exp_pay), (
            f"{len(exp_pay)} payload packets expected, got {len(self.sink.frames)}")


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_single_udp(dut):
    """One ordinary UDP datagram (IHL=5, 100-byte payload), no backpressure."""
    tb = Tb(dut)
    await tb.reset()
    tb.send([build_udp(rbytes(100), sport=12345, dport=26400)])
    await tb.finish()
    tb.check()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_payload_lengths(dut):
    """Payload lengths 0..40 with IHL=5 (payload starts at byte 42 = lane 2) and IHL=6
    (byte 46 = lane 6): every realignment case, incl. an empty payload (header only)."""
    tb = Tb(dut)
    await tb.reset()
    frames = [build_udp(rbytes(n), ihl=ihl) for ihl in (5, 6) for n in range(41)]
    tb.send(frames)
    await tb.finish()
    tb.check()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_ip_options(dut):
    """IHL 5..15 (0..40 bytes of IP options): the UDP header moves and straddles beats."""
    tb = Tb(dut)
    await tb.reset()
    frames = [build_udp(rbytes(random.randint(0, 90)), ihl=ihl) for ihl in range(5, 16) for _ in range(3)]
    tb.send(frames)
    await tb.finish()
    tb.check()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_padding_and_trailer(dut):
    """Short payloads padded to the 60-byte Ethernet minimum, plus random junk after the datagram:
    the payload must be cut at the UDP length."""
    tb = Tb(dut)
    await tb.reset()
    frames = [build_udp(rbytes(random.randint(0, 17)), ihl=random.choice([5, 6, 7]),
                        trailer=rbytes(random.randint(0, 12))) for _ in range(scale(60))]
    tb.send(frames)
    await tb.finish()
    tb.check()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_drop_non_udp(dut):
    """ARP, IPv6, VLAN-tagged, TCP, ICMP, bad version, IHL<5, UDP length<8, runts: all dropped,
    interleaved with good UDP frames that must still come out intact."""
    tb = Tb(dut)
    await tb.reset()
    frames = []
    for k in OTHER_KINDS * 3:
        frames.append(build_other(k))
        if random.random() < 0.5:
            frames.append(build_udp(rbytes(random.randint(0, 64))))
    tb.send(frames)
    await tb.finish()
    tb.check()


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_truncated_frames(dut):
    """UDP length claims more bytes than the frame holds: header still emitted, payload ends
    (tlast) at the last byte that arrived; with zero payload bytes present, no payload beat."""
    tb = Tb(dut)
    await tb.reset()
    frames = []
    for _ in range(scale(30)):
        n = random.randint(1, 80)
        f = build_udp(rbytes(n), ihl=random.choice([5, 6]), pad_to=0)
        cut = random.randint(len(f) - n, len(f) - 1)  # keep the whole header, drop >=1 payload byte
        frames.append(f[:cut])
    tb.send(frames)
    await tb.finish()
    tb.check()


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_random_mix_backpressure(dut):
    """Random mix of good/bad frames, random input bubbles, random m_tready and m_hdr_ready."""
    tb = Tb(dut, p_idle=0.2, p_ready=0.6, p_hdr_ready=0.4)
    await tb.reset()
    frames = []
    for _ in range(scale(150)):
        if random.random() < 0.7:
            frames.append(build_udp(rbytes(random.choice([random.randint(0, 30), random.randint(0, 400)])),
                                    ihl=random.choice([5, 5, 5, random.randint(5, 15)]),
                                    trailer=rbytes(random.choice([0, 0, random.randint(0, 9)]))))
        else:
            frames.append(build_other(random.choice(OTHER_KINDS)))
    tb.send(frames)
    await tb.finish(quiet_cycles=200)
    tb.check()


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def test_header_backpressure(dut):
    """m_hdr_ready held at 0 for a long time, then released: nothing may be lost or duplicated."""
    tb = Tb(dut, p_hdr_ready=0.0)
    await tb.reset()
    tb.send([build_udp(rbytes(random.randint(0, 100))) for _ in range(12)])
    for _ in range(600):
        await RisingEdge(dut.clk)
    tb.p_hdr_ready = 1.0
    await tb.finish()
    tb.check()


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def test_throughput(dut):
    """Back-to-back frames, no backpressure: logs the input stall cycles. The base spec only
    requires <= 4 stall cycles per frame on average; zero (full line rate) is a stretch goal."""
    tb = Tb(dut)
    await tb.reset()
    frames = [build_udp(rbytes(random.randint(0, 300)), ihl=random.choice([5, 6])) for _ in range(scale(80))]
    tb.send(frames)
    await tb.finish()
    tb.check()
    dut._log.info(f"input stall cycles: {tb.stall_cycles} over {len(frames)} frames "
                  f"({tb.stall_cycles / len(frames):.2f} per frame; 0 = line rate)")
    assert tb.stall_cycles <= 4 * len(frames), (
        f"{tb.stall_cycles} stall cycles for {len(frames)} frames: more than 4 per frame")


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_reset_mid_frame(dut):
    """Reset while a frame is half-way through: afterwards the parser starts clean."""
    tb = Tb(dut)
    await tb.reset()
    tb.src.send(build_udp(rbytes(200)))
    for _ in range(9):
        await RisingEdge(dut.clk)
    dut.rst.value = 1
    tb.src.queue.clear()
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    tb.hdrs.clear()
    tb.sink.frames.clear()
    tb.sink._cur = bytearray()
    tb.send([build_udp(rbytes(random.randint(0, 120)), ihl=random.choice([5, 6])) for _ in range(10)])
    await tb.finish()
    tb.check()
