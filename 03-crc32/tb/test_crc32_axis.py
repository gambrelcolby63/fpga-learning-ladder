"""cocotb testbench for crc32_axis (see ../README.md). Reference model: Python's zlib.crc32,
which implements exactly the Ethernet / IEEE 802.3 CRC-32 (reflected, init 0xFFFFFFFF,
final XOR 0xFFFFFFFF)."""
from __future__ import annotations

import random
import zlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

from ladder_tb import AxisSource, scale, u

RESIDUE = 0x2144DF1C   # CRC-32 of (any message + its own FCS, little-endian)
MAX_LAT = 3            # m_crc_valid may come 0..3 cycles after the cycle following the tlast edge


def with_fcs(msg: bytes) -> bytes:
    return msg + zlib.crc32(msg).to_bytes(4, "little")


class Tb:
    def __init__(self, dut, p_idle: float = 0.0):
        self.dut = dut
        dut.rst.value = 1
        Clock(dut.clk, 4, unit="ns").start()
        self.src = AxisSource(dut.clk, dut, "s_", 8, p_idle)
        self.expected: list[tuple[bytes, int]] = []   # (frame, cycle of the tlast edge)
        self.results: list[tuple[int, int, int]] = []  # (crc, ok, cycle seen)
        self.cycle = 0
        self.errors: list[str] = []
        self._cur = bytearray()
        self.monitoring = False
        cocotb.start_soon(self._monitor())

    async def _monitor(self):
        dut = self.dut
        while True:
            await RisingEdge(dut.clk)
            self.cycle += 1
            await ReadOnly()
            if u(dut.rst) or not self.monitoring:
                self._cur = bytearray()
                continue
            if u(dut.s_tready) != 1 and len(self.errors) < 10:
                self.errors.append(f"cycle {self.cycle}: s_tready=0 (must accept one beat every clock)")
            if u(dut.m_crc_valid):
                self.results.append((u(dut.m_crc), u(dut.m_crc_ok), self.cycle))
            if u(dut.s_tvalid) and u(dut.s_tready):
                k = u(dut.s_tkeep)
                n = k.bit_length()
                self._cur += u(dut.s_tdata).to_bytes(8, "little")[:n]
                if u(dut.s_tlast):
                    self.expected.append((bytes(self._cur), self.cycle + 1))
                    self._cur = bytearray()

    async def reset(self):
        self.dut.rst.value = 1
        for _ in range(3):
            await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        self.monitoring = True

    async def run(self, frames: list[bytes]):
        for f in frames:
            self.src.send(f)
        n = 0
        while self.src.queue or u(self.dut.s_tvalid):
            await RisingEdge(self.dut.clk)
            n += 1
            assert n < 100000, "source never drained (s_tready stuck low?)"
        for _ in range(MAX_LAT + 4):
            await RisingEdge(self.dut.clk)

    def check(self, frames: list[bytes] | None = None):
        assert not self.errors, "\n".join(self.errors)
        if frames is not None:
            got_frames = [f for f, _ in self.expected]
            assert got_frames == frames, "testbench bug: monitor saw different frames than were sent"
        assert len(self.results) == len(self.expected), (
            f"{len(self.expected)} frames sent but {len(self.results)} m_crc_valid pulses seen")
        for i, ((frame, t_last), (crc, ok, t)) in enumerate(zip(self.expected, self.results)):
            exp = zlib.crc32(frame)
            assert crc == exp, (f"frame #{i} ({len(frame)} bytes, {frame[:16].hex()}...): m_crc={crc:#010x}, "
                                f"expected {exp:#010x}")
            assert ok == int(exp == RESIDUE), f"frame #{i}: m_crc_ok={ok} but m_crc={crc:#010x}"
            lat = t - t_last
            assert 0 <= lat <= MAX_LAT, (f"frame #{i}: m_crc_valid came {lat} cycles after the tlast beat's edge "
                                         f"(allowed 0..{MAX_LAT})")


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_check_value(dut):
    """The CRC catalogue 'check' value: CRC-32("123456789") = 0xCBF43926, plus a few tiny frames."""
    tb = Tb(dut)
    await tb.reset()
    frames = [b"123456789", b"\x00", b"\xff", b"\x00" * 8, b"\xff" * 8, b"a"]
    await tb.run(frames)
    tb.check(frames)
    assert tb.results[0][0] == 0xCBF43926


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_every_length(dut):
    """Frame lengths 1..80: every tkeep pattern on the last beat, 1..10 beats per frame."""
    tb = Tb(dut)
    await tb.reset()
    frames = [bytes(random.getrandbits(8) for _ in range(n)) for n in range(1, 81)]
    await tb.run(frames)
    tb.check(frames)


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def test_good_fcs(dut):
    """Random Ethernet-sized frames ending in a correct FCS: m_crc_ok=1, m_crc=0x2144DF1C."""
    tb = Tb(dut)
    await tb.reset()
    frames = [with_fcs(bytes(random.getrandbits(8) for _ in range(random.randint(56, 400))))
              for _ in range(scale(60))]
    frames.append(with_fcs(bytes(random.getrandbits(8) for _ in range(1514))))  # max standard frame
    await tb.run(frames)
    tb.check(frames)
    assert all(ok for _, ok, _ in tb.results)


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def test_bad_fcs(dut):
    """Same, but with one to three random bit errors anywhere in the frame (incl. the FCS): m_crc_ok=0."""
    tb = Tb(dut)
    await tb.reset()
    frames = []
    for _ in range(scale(60)):
        f = bytearray(with_fcs(bytes(random.getrandbits(8) for _ in range(random.randint(1, 300)))))
        for _ in range(random.randint(1, 3)):
            f[random.randrange(len(f))] ^= 1 << random.randrange(8)
        frames.append(bytes(f))
    await tb.run(frames)
    tb.check(frames)


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_back_to_back_single_beat(dut):
    """1..8-byte frames on consecutive clocks (tlast on every beat): one result per clock."""
    tb = Tb(dut)
    await tb.reset()
    frames = [bytes(random.getrandbits(8) for _ in range(random.randint(1, 8))) for _ in range(scale(200))]
    await tb.run(frames)
    tb.check(frames)


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def test_bubbles(dut):
    """s_tvalid drops randomly (50%) inside frames; junk on the bus while tvalid=0 must be ignored."""
    tb = Tb(dut, p_idle=0.5)
    await tb.reset()
    frames = []
    for _ in range(scale(80)):
        m = bytes(random.getrandbits(8) for _ in range(random.randint(1, 120)))
        frames.append(with_fcs(m) if random.random() < 0.5 else m)
    await tb.run(frames)
    tb.check(frames)


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_reset_mid_frame(dut):
    """Reset in the middle of a frame: the partial frame produces no result and the next frame's CRC
    starts from a clean state."""
    tb = Tb(dut)
    await tb.reset()
    tb.src.send(bytes(range(40)))
    for _ in range(3):
        await RisingEdge(dut.clk)
    tb.monitoring = False
    dut.rst.value = 1
    tb.src.queue.clear()
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(4):
        await RisingEdge(dut.clk)
    tb.expected.clear()
    tb.results.clear()
    await RisingEdge(dut.clk)
    tb.monitoring = True
    frames = [with_fcs(bytes(random.getrandbits(8) for _ in range(random.randint(1, 100)))) for _ in range(10)]
    await tb.run(frames)
    tb.check(frames)
