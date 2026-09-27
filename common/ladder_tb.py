"""Small shared helpers for the ladder testbenches (cocotb 2.x).

Timing convention used by every driver/monitor here:
  * right after a rising clock edge the testbench drives new input values;
  * in the ReadOnly phase of the same time step (everything settled) it samples outputs and
    decides whether a valid/ready handshake will happen at the *next* rising edge.
This avoids any dependence on simulator-specific ordering at the clock edge itself.
"""
from __future__ import annotations

import os
import random
from collections import deque
from typing import Callable

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge


def param(name: str, default: int | None = None) -> int:
    """HDL parameter value (the runner exports PARAM_<name>)."""
    v = os.environ.get(f"PARAM_{name}")
    if v is None:
        if default is None:
            raise RuntimeError(f"PARAM_{name} not set")
        return default
    return int(v.replace("_", ""), 0)


def scale(n: int) -> int:
    """Scale a randomized test length with the LADDER_SCALE env var (default 1.0)."""
    return max(1, int(n * float(os.environ.get("LADDER_SCALE") or "1")))


def u(sig) -> int:
    """Signal value as an unsigned int; raises a readable error on X/Z."""
    v = sig.value
    try:
        return int(v)
    except ValueError:
        raise AssertionError(f"{sig._name} is X/Z ({v}) where a defined value was expected") from None


def resolvable(sig) -> bool:
    return sig.value.is_resolvable


async def reset_sync(clk, rst, cycles: int = 5, active_high: bool = True) -> None:
    rst.value = 1 if active_high else 0
    for _ in range(cycles):
        await RisingEdge(clk)
    rst.value = 0 if active_high else 1
    await RisingEdge(clk)


def bytes_to_beats(frame: bytes, width_bytes: int = 8) -> list[tuple[int, int, int]]:
    """Split a frame into AXI-Stream beats: list of (tdata, tkeep, tlast). Byte 0 -> tdata[7:0]."""
    beats = []
    for i in range(0, len(frame), width_bytes):
        chunk = frame[i:i + width_bytes]
        data = int.from_bytes(chunk, "little")
        keep = (1 << len(chunk)) - 1
        last = int(i + width_bytes >= len(frame))
        beats.append((data, keep, last))
    return beats


class AxisSource:
    """Drives s_tdata/s_tkeep/s_tvalid/s_tlast; honours s_tready. Random idle cycles if p_idle>0.
    With garbage=True (default) the byte lanes that tkeep marks invalid, and every signal while
    tvalid=0, carry random junk -- legal AXI-Stream, and it catches designs that forget to mask."""

    def __init__(self, clk, dut, prefix: str = "s_", width_bytes: int = 8, p_idle: float = 0.0,
                 garbage: bool = True):
        self.clk = clk
        self.tdata = getattr(dut, prefix + "tdata")
        self.tkeep = getattr(dut, prefix + "tkeep")
        self.tvalid = getattr(dut, prefix + "tvalid")
        self.tlast = getattr(dut, prefix + "tlast")
        self.tready = getattr(dut, prefix + "tready", None)
        self.wb = width_bytes
        self.p_idle = p_idle
        self.garbage = garbage
        self.queue: deque[tuple[int, int, int]] = deque()
        self.beats_sent = 0
        self.tvalid.value = 0
        self.tdata.value = 0
        self.tkeep.value = 0
        self.tlast.value = 0
        self._task = cocotb.start_soon(self._run())

    def send(self, frame: bytes) -> None:
        self.queue.extend(bytes_to_beats(frame, self.wb))

    def idle(self) -> bool:
        return not self.queue and not int(self.tvalid.value)

    async def _run(self) -> None:
        cur = None
        while True:
            await RisingEdge(self.clk)
            if cur is None and self.queue and random.random() >= self.p_idle:
                cur = self.queue.popleft()
            if cur is None:
                self.tvalid.value = 0
                if self.garbage:
                    self.tdata.value = random.getrandbits(8 * self.wb)
                    self.tkeep.value = random.getrandbits(self.wb)
                    self.tlast.value = random.getrandbits(1)
            else:
                d, k, l = cur
                if self.garbage:
                    n = k.bit_length()
                    d |= random.getrandbits(8 * self.wb) & ~((1 << (8 * n)) - 1) & ((1 << (8 * self.wb)) - 1)
                self.tdata.value = d
                self.tkeep.value = k
                self.tlast.value = l
                self.tvalid.value = 1
            await ReadOnly()
            if cur is not None and (self.tready is None or u(self.tready)):
                cur = None  # handshake happens at the next rising edge
                self.beats_sent += 1


class AxisSink:
    """Drives m_tready (random if p_ready<1), collects frames, and checks AXI-Stream rules:
    tvalid must not drop / payload must not change while stalled; tkeep contiguous from bit 0;
    only the last beat of a frame may be partial."""

    def __init__(self, clk, dut, prefix: str = "m_", width_bytes: int = 8, p_ready: float = 1.0,
                 ready_fn: Callable[[], bool] | None = None):
        self.clk = clk
        self.tdata = getattr(dut, prefix + "tdata")
        self.tkeep = getattr(dut, prefix + "tkeep")
        self.tvalid = getattr(dut, prefix + "tvalid")
        self.tlast = getattr(dut, prefix + "tlast")
        self.tready = getattr(dut, prefix + "tready")
        self.wb = width_bytes
        self.p_ready = p_ready
        self.ready_fn = ready_fn
        self.frames: list[bytes] = []
        self._cur = bytearray()
        self.errors: list[str] = []
        self.tready.value = 0
        self._task = cocotb.start_soon(self._run())

    async def _run(self) -> None:
        stalled = None  # (data, keep, last) of a beat that was valid but not accepted
        while True:
            await RisingEdge(self.clk)
            if self.ready_fn is not None:
                r = self.ready_fn()
            else:
                r = random.random() < self.p_ready
            self.tready.value = int(r)
            await ReadOnly()
            if not resolvable(self.tvalid):
                continue  # during reset
            v = u(self.tvalid)
            if stalled is not None:
                if not v:
                    self.errors.append("m_tvalid dropped before the beat was accepted (AXI-Stream rule)")
                else:
                    now = (u(self.tdata), u(self.tkeep), u(self.tlast))
                    if now != stalled:
                        self.errors.append(f"beat changed while stalled: was {stalled}, now {now}")
            stalled = None
            if v:
                d, k, l = u(self.tdata), u(self.tkeep), u(self.tlast)
                if r:
                    n = 0
                    while n < self.wb and (k >> n) & 1:
                        n += 1
                    if k != (1 << n) - 1 or n == 0:
                        self.errors.append(f"tkeep {k:#x} not contiguous from bit 0 (or zero)")
                    if not l and n != self.wb:
                        self.errors.append(f"non-last beat with partial tkeep {k:#x}")
                    self._cur += (d & ((1 << (8 * n)) - 1)).to_bytes(self.wb, "little")[:n]
                    if l:
                        self.frames.append(bytes(self._cur))
                        self._cur = bytearray()
                else:
                    stalled = (d, k, l)


async def wait_until(clk, cond: Callable[[], bool], timeout_cycles: int, what: str) -> None:
    for _ in range(timeout_cycles):
        if cond():
            return
        await RisingEdge(clk)
    if not cond():
        raise AssertionError(f"timeout after {timeout_cycles} cycles waiting for: {what}")
