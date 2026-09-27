"""cocotb testbench for uart_rx (see ../README.md for the spec it checks).

The serial line is driven from Python with picosecond-resolution delays, i.e. completely
asynchronously to clk, optionally with a baud-rate error, like a real remote transmitter.
"""
from __future__ import annotations

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from ladder_tb import param, scale, u
from uart_model import clks_per_bit, frame_bits

CLK_PS = 10_000
CPB = clks_per_bit(param("CLK_FREQ_HZ"), param("BAUD"))
BIT_PS = CPB * CLK_PS
# Baud-rate error the receiver must tolerate (see README "Receiver tolerance").
TOL = 0.02 if CPB < 32 else 0.03 if CPB < 64 else 0.035
# backstop so a broken design can never hang the regression (simulated time)
TIMEOUT_US = 400 * BIT_PS * 3 // 1_000_000


def now_ps() -> int:
    return get_sim_time(unit="ps")


class Tb:
    def __init__(self, dut):
        self.dut = dut
        self.events: list[tuple[str, int, int]] = []  # observed: (kind, data, time_ps)
        self.expected: list[tuple[str, int, int, int]] = []  # (kind, data, t_min_ps, t_max_ps)
        dut.rx.value = 1
        dut.rst.value = 1
        Clock(dut.clk, CLK_PS, unit="ps").start()
        cocotb.start_soon(self._monitor())

    async def _monitor(self):
        dut = self.dut
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if u(dut.rst):
                continue
            v, fe = u(dut.m_valid), u(dut.frame_err)
            if v and fe:
                self.events.append(("both", 0, now_ps()))
            elif v:
                self.events.append(("byte", u(dut.m_data), now_ps()))
            elif fe:
                self.events.append(("ferr", 0, now_ps()))

    async def reset(self):
        self.dut.rst.value = 1
        for _ in range(4):
            await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        for _ in range(4):
            await RisingEdge(self.dut.clk)
        await Timer(random.randrange(1, CLK_PS), unit="ps")  # random phase vs clk

    async def idle(self, bits: float):
        self.dut.rx.value = 1
        ps = int(bits * BIT_PS)
        if ps > 0:
            await Timer(ps, unit="ps")

    async def send(self, byte: int, err: float = 0.0, stop_val: int = 1, extra_low_bits: float = 0.0):
        """Drive one frame. err = relative bit-time error of the remote transmitter
        (+0.03 = 3% slow). If stop_val=0 a framing error is expected."""
        bit = int(round(BIT_PS * (1 + err)))
        t0 = now_ps()
        for level in frame_bits(byte, stop_val):
            self.dut.rx.value = level
            await Timer(bit, unit="ps")
        if extra_low_bits:
            await Timer(int(extra_low_bits * bit), unit="ps")
        # The result must appear after the stop bit has started (you can't know the frame is
        # good before sampling it) and no later than the end of the stop bit + 4 clocks.
        t_min = t0 + 9 * bit
        t_max = t0 + 10 * bit + 4 * CLK_PS
        self.expected.append(("byte" if stop_val else "ferr", byte if stop_val else 0, t_min, t_max))
        self.dut.rx.value = 1

    async def settle(self, bits: float = 3):
        await self.idle(bits)
        for _ in range(4):
            await RisingEdge(self.dut.clk)

    def check(self):
        exp, got = self.expected, self.events
        kinds_e = [(k, d) for k, d, _, _ in exp]
        kinds_g = [(k, d) for k, d, _ in got]
        assert all(k != "both" for k, _ in kinds_g), "m_valid and frame_err asserted in the same cycle"
        if kinds_e != kinds_g:
            def fmt(lst):
                return [f"{k}:{d:#04x}" if k == "byte" else k for k, d in lst]
            raise AssertionError(
                f"received events don't match what was sent\n"
                f"  expected ({len(kinds_e)}): {fmt(kinds_e)}\n"
                f"  got      ({len(kinds_g)}): {fmt(kinds_g)}\n"
                "  (a byte appearing twice usually means m_valid was high for more than one cycle)")
        for (k, d, tmin, tmax), (_, _, t) in zip(exp, got):
            assert tmin <= t <= tmax, (
                f"{k} {d:#04x}: output at t={t / 1000:.1f} ns, expected between {tmin / 1000:.1f} ns "
                f"(start of stop bit) and {tmax / 1000:.1f} ns (end of stop bit + 4 clocks)")


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_directed_bytes(dut):
    """Corner-case bytes at the nominal baud rate with idle time between frames."""
    tb = Tb(dut)
    await tb.reset()
    await tb.idle(2)
    for b in [0x00, 0xFF, 0x55, 0xAA, 0x01, 0x80, 0x0F, 0xF0, 0x7E]:
        await tb.send(b)
        await tb.idle(1 + random.random())
    await tb.settle()
    tb.check()


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_back_to_back(dut):
    """Frames with exactly one stop bit and no idle time between them."""
    tb = Tb(dut)
    await tb.reset()
    await tb.idle(1)
    for _ in range(scale(48)):
        await tb.send(random.randrange(256))
    await tb.settle()
    tb.check()


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_fast_transmitter(dut):
    """Remote transmitter's clock is fast by TOL (bits shorter than nominal), back-to-back frames."""
    tb = Tb(dut)
    await tb.reset()
    await tb.idle(1)
    for _ in range(scale(40)):
        await tb.send(random.randrange(256), err=-TOL)
    await tb.settle()
    tb.check()


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_slow_transmitter(dut):
    """Remote transmitter's clock is slow by TOL (bits longer than nominal), back-to-back frames.
    This is what catches a receiver that samples too early in each bit."""
    tb = Tb(dut)
    await tb.reset()
    await tb.idle(1)
    for _ in range(scale(40)):
        await tb.send(random.randrange(256), err=+TOL)
    await tb.settle()
    tb.check()


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_random_traffic(dut):
    """Random bytes, random (fractional) idle gaps, random baud error within +/-TOL."""
    tb = Tb(dut)
    await tb.reset()
    await tb.idle(1)
    for _ in range(scale(80)):
        await tb.send(random.randrange(256), err=random.uniform(-TOL, TOL))
        await tb.idle(random.choice([0, 0, 0, random.uniform(0, 3)]))
    await tb.settle()
    tb.check()


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_glitch_rejected(dut):
    """Short low pulses on an idle line (< 1/3 bit) are noise, not start bits: no output at all.
    Then a real byte must still be received."""
    tb = Tb(dut)
    await tb.reset()
    await tb.idle(2)
    for _ in range(8):
        dut.rx.value = 0
        await Timer(random.randrange(CLK_PS, BIT_PS // 3), unit="ps")
        await tb.idle(2 + random.random())
    await tb.send(0x42)
    await tb.settle()
    tb.check()


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_framing_error(dut):
    """Stop bit sampled low -> one frame_err pulse and NO m_valid for that frame; the receiver
    then recovers and receives the next frames normally."""
    tb = Tb(dut)
    await tb.reset()
    await tb.idle(2)
    await tb.send(0x3C)
    await tb.idle(2)
    await tb.send(0xA5, stop_val=0)          # bad stop bit, line goes high right after
    await tb.idle(2)
    await tb.send(0x00, stop_val=0, extra_low_bits=1.5)  # line stays low past the stop bit
    await tb.idle(2)
    await tb.send(0x5A)
    await tb.idle(1)
    await tb.send(0xC3)
    await tb.settle()
    tb.check()


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_break_condition(dut):
    """Line held low for 25 bit times (a 'break'): exactly ONE frame_err, no bytes, and the
    receiver must wait for the line to go high again before hunting for the next start bit."""
    tb = Tb(dut)
    await tb.reset()
    await tb.idle(2)
    t0 = now_ps()
    dut.rx.value = 0
    await Timer(25 * BIT_PS, unit="ps")
    tb.expected.append(("ferr", 0, t0 + 9 * BIT_PS, t0 + 10 * BIT_PS + 4 * CLK_PS))
    await tb.idle(2)
    await tb.send(0x81)
    await tb.send(0x7F)
    await tb.settle()
    tb.check()


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_no_output_during_reset(dut):
    """Frames arriving while rst=1 are ignored; the first frame after reset is received."""
    tb = Tb(dut)
    dut.rst.value = 1
    await tb.idle(1)
    await tb.send(0x11)
    tb.expected.clear()
    await tb.idle(1)
    dut.rst.value = 0
    await tb.idle(2)
    await tb.send(0x22)
    await tb.settle()
    tb.check()
