"""cocotb testbench for uart_tx (see ../README.md for the spec it checks)."""
from __future__ import annotations

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

from ladder_tb import param, scale, u
from uart_model import clks_per_bit, decode_tx_trace

CLK_NS = 10
CPB = clks_per_bit(param("CLK_FREQ_HZ"), param("BAUD"))
FRAME = 10 * CPB
# backstop so a broken design can never hang the regression (simulated time)
TIMEOUT_US = (60 * FRAME + 2000) * CLK_NS * 3 // 1000


class Tb:
    def __init__(self, dut):
        self.dut = dut
        self.tx: list[int] = []
        self.busy: list[int] = []
        self.accepted: list[tuple[int, int]] = []  # (cycle index of handshake, byte)
        self.recording = False
        dut.s_valid.value = 0
        dut.s_data.value = 0
        dut.rst.value = 1
        Clock(dut.clk, CLK_NS, unit="ns").start()
        cocotb.start_soon(self._monitor())

    async def _monitor(self):
        while True:
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            if not self.recording:
                continue
            self.tx.append(u(self.dut.tx))
            self.busy.append(u(self.dut.busy))
            if u(self.dut.s_valid) and u(self.dut.s_ready):
                # handshake completes at the next edge; tag it with the next sample index
                self.accepted.append((len(self.tx), u(self.dut.s_data)))

    async def reset(self):
        self.dut.rst.value = 1
        for _ in range(4):
            await RisingEdge(self.dut.clk)
        self.dut.rst.value = 0
        self.recording = True

    async def send(self, data: list[int], gaps: list[int] | None = None, garbage: bool = True):
        """valid/ready handshake per byte. While s_valid=0 (and right after each handshake) the
        s_data bus carries garbage, so a design that doesn't capture s_data at the handshake fails."""
        dut = self.dut
        for n, b in enumerate(data):
            gap = gaps[n] if gaps else 0
            for _ in range(gap):
                dut.s_valid.value = 0
                if garbage:
                    dut.s_data.value = random.randrange(256)
                await RisingEdge(dut.clk)
            dut.s_valid.value = 1
            dut.s_data.value = b
            await ReadOnly()
            waited = 0
            while not u(dut.s_ready):
                waited += 1
                assert waited < 2 * FRAME + 10, f"s_ready never asserted (waited {waited} clocks for byte #{n})"
                await RisingEdge(dut.clk)
                await ReadOnly()
            await RisingEdge(dut.clk)  # handshake happens on this edge
            dut.s_valid.value = 0
            if garbage:
                dut.s_data.value = random.randrange(256)

    async def drain(self, extra: int = 3 * CPB):
        """Wait until the line has been idle (tx=1, busy=0) for `extra` cycles."""
        idle = 0
        for _ in range(20 * FRAME + extra * 4):
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            idle = idle + 1 if (u(self.dut.tx) == 1 and u(self.dut.busy) == 0) else 0
            if idle >= extra:
                return
        raise AssertionError("transmitter never went idle")

    def check(self, expect_back_to_back: bool = False):
        frames, errors = decode_tx_trace(self.tx, CPB)
        assert not errors, "tx line errors:\n  " + "\n  ".join(errors)
        sent = [b for _, b in self.accepted]
        got = [b for _, b in frames]
        assert got == sent, (f"bytes on the tx line don't match accepted bytes\n"
                             f"  accepted ({len(sent)}): {[hex(x) for x in sent]}\n"
                             f"  on line  ({len(got)}): {[hex(x) for x in got]}")
        for (acc_idx, _), (start, _) in zip(self.accepted, frames):
            assert start >= acc_idx, f"frame at cycle {start} started before its byte was accepted ({acc_idx})"
        for start, _ in frames:
            for i in range(start, start + FRAME):
                assert self.busy[i] == 1, (f"busy=0 at cycle {i}, inside the frame that started at cycle "
                                           f"{start} (busy must be 1 from the start bit to the end of the stop bit)")
        if expect_back_to_back:
            for (s0, _), (s1, _) in zip(frames, frames[1:]):
                gap = s1 - s0
                assert FRAME <= gap <= FRAME + 2, (
                    f"back-to-back frames start {gap} clocks apart; expected {FRAME}..{FRAME + 2} "
                    f"(10 bits x {CPB} clocks, at most 2 idle clocks)")
        return frames


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_reset_and_idle(dut):
    """After reset: tx idles high, busy=0, s_ready=1 and nothing is transmitted."""
    tb = Tb(dut)
    await tb.reset()
    for _ in range(3):
        await RisingEdge(dut.clk)
    await ReadOnly()
    assert u(dut.s_ready) == 1, "s_ready should be 1 when idle after reset"
    assert u(dut.busy) == 0, "busy should be 0 when idle after reset"
    for _ in range(3 * CPB):
        await RisingEdge(dut.clk)
    tb.check()
    assert all(t == 1 for t in tb.tx), "tx must stay high (idle) when nothing is sent"


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_directed_bytes(dut):
    """Corner-case bytes, one at a time with idle gaps. Checks exact bit timing and LSB-first order."""
    tb = Tb(dut)
    await tb.reset()
    data = [0x00, 0xFF, 0x55, 0xAA, 0x01, 0x80, 0x0F, 0xF0, 0x3C]
    await tb.send(data, gaps=[CPB * 2 + random.randrange(CPB) for _ in data])
    await tb.drain()
    frames = tb.check()
    assert len(frames) == len(data)


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_back_to_back(dut):
    """s_valid held high: frames must follow each other with <= 2 idle clocks between them."""
    tb = Tb(dut)
    await tb.reset()
    n = scale(24) if CPB < 100 else scale(6)
    data = [random.randrange(256) for _ in range(n)]
    await tb.send(data)
    await tb.drain()
    frames = tb.check(expect_back_to_back=True)
    assert len(frames) == n


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_random_traffic(dut):
    """Random bytes with random gaps (0..1.5 frames), garbage on s_data between handshakes."""
    tb = Tb(dut)
    await tb.reset()
    n = scale(40) if CPB < 100 else scale(8)
    data = [random.randrange(256) for _ in range(n)]
    gaps = [random.choice([0, 0, 1, 2, random.randrange(FRAME + FRAME // 2)]) for _ in range(n)]
    await tb.send(data, gaps)
    await tb.drain()
    tb.check()


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_ready_only_when_able(dut):
    """Holding s_valid high for a long time must yield exactly one frame per handshake."""
    tb = Tb(dut)
    await tb.reset()
    await tb.send([0xA5, 0x5A, 0xC3])
    await tb.drain()
    frames = tb.check()
    assert [b for _, b in frames] == [0xA5, 0x5A, 0xC3]
    assert len(tb.accepted) == 3


@cocotb.test(timeout_time=TIMEOUT_US, timeout_unit="us")
async def test_reset_mid_frame(dut):
    """Synchronous reset in the middle of a frame: tx returns high immediately and the
    transmitter works normally afterwards."""
    tb = Tb(dut)
    await tb.reset()
    await tb.send([0x00])
    for _ in range(3 * CPB + CPB // 2):
        await RisingEdge(dut.clk)
    dut.rst.value = 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert u(dut.tx) == 1, "tx must be 1 (idle) in the cycle after reset is sampled"
    await RisingEdge(dut.clk)
    dut.rst.value = 0
    for _ in range(2 * CPB):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert u(dut.tx) == 1, "tx must stay idle after reset"
    # fresh trace from here
    tb.tx.clear(); tb.busy.clear(); tb.accepted.clear()
    await RisingEdge(dut.clk)
    await tb.send([0x96, 0x69])
    await tb.drain()
    frames = tb.check()
    assert [b for _, b in frames] == [0x96, 0x69]
