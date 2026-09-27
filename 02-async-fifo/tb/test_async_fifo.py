"""cocotb testbench for async_fifo (see ../README.md for the spec it checks).

Two free-running, unrelated clocks. A Python scoreboard models the FIFO contents and, at every
clock edge of each domain, checks:
  * data integrity / ordering (every accepted write is read back exactly once, in order);
  * r_empty is never 0 when the FIFO holds no complete entry (no underflow);
  * w_full is never 0 when the FIFO holds DEPTH entries (no overflow);
  * the required internal Gray pointers (wptr_gray, rptr_gray, rq2_wptr_gray, wq2_rptr_gray)
    are the Gray code of the true write/read counts, and the synchronized copies are sane.
"""
from __future__ import annotations

import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

from ladder_tb import param, scale, u

WIDTH = param("WIDTH")
ADDR_W = param("ADDR_W")
DEPTH = 1 << ADDR_W
MOD = 2 * DEPTH  # pointers are ADDR_W+1 bits wide

CLOCKS = {  # name: (wclk period ps, rclk period ps)
    "write_faster": (4000, 10000),
    "read_faster": (10000, 3000),
    "nearly_equal": (10000, 10010),
    "same_freq": (8000, 8000),
    "unrelated": (7000, 13002),
}


def gray(b: int) -> int:
    return b ^ (b >> 1)


def gray2bin(g: int) -> int:
    b = 0
    while g:
        b ^= g
        g >>= 1
    return b


def internal(dut, name):
    try:
        return getattr(dut, name)
    except AttributeError:
        raise AssertionError(
            f"internal signal '{name}' not found: the spec requires the four Gray-pointer registers to be "
            "named wptr_gray, rptr_gray, rq2_wptr_gray and wq2_rptr_gray (see README)") from None


class Tb:
    def __init__(self, dut, wper: int, rper: int):
        self.dut = dut
        self.wper, self.rper = wper, rper
        self.q: deque[int] = deque()   # model contents (includes a write that completes at next edge)
        self.w_done = 0                # writes completed (edges already passed)
        self.r_done = 0
        self.w_pending = False
        self.r_pending = False
        self.accepted = 0
        self.popped = 0
        self.errors: list[str] = []
        self.checking = False
        self.p_w = 0.0
        self.p_r = 0.0
        self.w_budget = 0              # how many more writes the random driver may attempt
        self.wptr = internal(dut, "wptr_gray")
        self.rptr = internal(dut, "rptr_gray")
        self.rq2 = internal(dut, "rq2_wptr_gray")
        self.wq2 = internal(dut, "wq2_rptr_gray")
        dut.w_en.value = 0
        dut.r_en.value = 0
        dut.w_data.value = 0
        dut.wrst_n.value = 0
        dut.rrst_n.value = 0

    async def start(self):
        Clock(self.dut.wclk, self.wper, unit="ps").start()
        await Timer(random.randrange(1, self.rper), unit="ps")  # random phase between domains
        Clock(self.dut.rclk, self.rper, unit="ps").start()
        cocotb.start_soon(self._wmon())
        cocotb.start_soon(self._rmon())
        await self.reset()

    def err(self, msg: str):
        if len(self.errors) < 20:
            self.errors.append(f"t={get_sim_time(unit='ns'):.1f}ns: {msg}")

    async def reset(self):
        """Assert both resets asynchronously, hold, release each synchronously to its clock."""
        self.checking = False
        self.dut.wrst_n.value = 0
        self.dut.rrst_n.value = 0
        self.dut.w_en.value = 0
        self.dut.r_en.value = 0
        for _ in range(4):
            await RisingEdge(self.dut.wclk)
            await RisingEdge(self.dut.rclk)
        self.q.clear()
        self.w_done = self.r_done = 0
        self.w_pending = self.r_pending = False
        await RisingEdge(self.dut.wclk)
        self.dut.wrst_n.value = 1
        await RisingEdge(self.dut.rclk)
        self.dut.rrst_n.value = 1
        await RisingEdge(self.dut.wclk)
        await RisingEdge(self.dut.rclk)
        await ReadOnly()
        if u(self.dut.r_empty) != 1:
            self.err("r_empty must be 1 after reset")
        if u(self.dut.w_full) != 0:
            self.err("w_full must be 0 after reset")
        self.checking = True
        await RisingEdge(self.dut.wclk)

    async def _wmon(self):
        dut = self.dut
        while True:
            await RisingEdge(dut.wclk)
            if self.w_pending:
                self.w_done += 1
                self.w_pending = False
            await ReadOnly()
            if not self.checking:
                continue
            wp = u(self.wptr)
            if wp != gray(self.w_done % MOD):
                self.err(f"wptr_gray={wp:#x} but {self.w_done} writes have completed "
                         f"(expected Gray code {gray(self.w_done % MOD):#x})")
            b = gray2bin(u(self.wq2))
            if (self.r_done - b) % MOD > DEPTH:
                self.err(f"wq2_rptr_gray={u(self.wq2):#x} (binary {b}) is not a recent value of the read "
                         f"pointer (reads completed: {self.r_done})")
            occ = self.w_done - self.r_done
            full = u(dut.w_full)
            if not full and occ >= DEPTH:
                self.err(f"w_full=0 but the FIFO already holds {occ} entries (DEPTH={DEPTH}) -> overflow")
            if u(dut.w_en) and not full:
                self.q.append(u(dut.w_data))
                self.w_pending = True
                self.accepted += 1

    async def _rmon(self):
        dut = self.dut
        while True:
            await RisingEdge(dut.rclk)
            if self.r_pending:
                self.r_done += 1
                self.r_pending = False
            await ReadOnly()
            if not self.checking:
                continue
            rp = u(self.rptr)
            if rp != gray(self.r_done % MOD):
                self.err(f"rptr_gray={rp:#x} but {self.r_done} reads have completed "
                         f"(expected Gray code {gray(self.r_done % MOD):#x})")
            b = gray2bin(u(self.rq2))
            if (self.w_done - b) % MOD > DEPTH or (b - self.r_done) % MOD > DEPTH:
                self.err(f"rq2_wptr_gray={u(self.rq2):#x} (binary {b}) is not a recent value of the write "
                         f"pointer (writes completed: {self.w_done}, reads: {self.r_done})")
            occ = self.w_done - self.r_done
            empty = u(dut.r_empty)
            if not empty and occ <= 0:
                self.err("r_empty=0 but the FIFO holds no completed write -> underflow")
            if u(dut.r_en) and not empty:
                if not self.q:
                    self.err("read accepted but the model FIFO is empty")
                else:
                    exp = self.q.popleft()
                    got = u(dut.r_data)
                    if got != exp:
                        self.err(f"read #{self.popped}: r_data={got:#x}, expected {exp:#x}")
                self.r_pending = True
                self.popped += 1

    async def wdrive(self):
        dut = self.dut
        while True:
            await RisingEdge(dut.wclk)
            en = self.w_budget > 0 and random.random() < self.p_w
            dut.w_en.value = int(en)
            dut.w_data.value = random.randrange(1 << WIDTH)  # garbage when w_en=0
            if en:
                self.w_budget -= 1  # attempts, not acceptances: attempts while full are dropped

    async def rdrive(self):
        dut = self.dut
        while True:
            await RisingEdge(dut.rclk)
            dut.r_en.value = int(random.random() < self.p_r)  # may assert r_en while empty

    def check(self):
        assert not self.errors, "scoreboard errors:\n  " + "\n  ".join(self.errors)


async def run_traffic(tb: Tb, words: int):
    wd = cocotb.start_soon(tb.wdrive())
    rd = cocotb.start_soon(tb.rdrive())
    phases = [(0.9, 0.2), (0.2, 0.9), (0.5, 0.5), (1.0, 1.0), (1.0, 0.05), (0.05, 1.0), (0.7, 0.7)]
    slow = max(tb.wper, tb.rper)
    per_phase = max(1, words // len(phases))
    for p_w, p_r in phases:
        tb.p_w, tb.p_r = p_w, p_r
        tb.w_budget = per_phase
        while tb.w_budget > 0:
            await Timer(20 * slow, unit="ps")
    # drain everything
    tb.p_w, tb.p_r = 0.0, 1.0
    for _ in range(4 * DEPTH + 40):
        await RisingEdge(tb.dut.rclk)
    wd.cancel()
    rd.cancel()
    await ReadOnly()
    tb.dut._log.info(f"traffic done: {tb.accepted} words written and read back")
    assert tb.popped == tb.accepted, f"{tb.accepted} writes accepted but only {tb.popped} read back"
    assert not tb.q, f"{len(tb.q)} entries never came out of the FIFO"
    assert u(tb.dut.r_empty) == 1, "r_empty should be 1 once everything is read"


@cocotb.test(timeout_time=20, timeout_unit="ms")
async def test_reset_then_one_word(dut):
    """After reset: r_empty=1, w_full=0, all four Gray pointers are 0. Then a single word goes
    through the FIFO and every pointer ends at 1 (Gray 0b...01)."""
    tb = Tb(dut, 10000, 7000)
    await tb.start()
    await ReadOnly()
    for s in (tb.wptr, tb.rptr, tb.rq2, tb.wq2):
        assert u(s) == 0, f"{s._name} should be 0 after reset"
    await RisingEdge(dut.wclk)
    dut.w_en.value = 1
    dut.w_data.value = word = random.randrange(1 << WIDTH)
    await RisingEdge(dut.wclk)
    dut.w_en.value = 0
    for _ in range(8):
        await RisingEdge(dut.rclk)
    await ReadOnly()
    assert u(dut.r_empty) == 0, "r_empty still 1 eight rclk cycles after a write"
    assert u(dut.r_data) == word, f"r_data={u(dut.r_data):#x}, expected {word:#x} (first-word-fall-through)"
    await RisingEdge(dut.rclk)
    dut.r_en.value = 1
    await RisingEdge(dut.rclk)
    dut.r_en.value = 0
    for _ in range(8):
        await RisingEdge(dut.wclk)
    await ReadOnly()
    assert u(dut.r_empty) == 1, "r_empty should be 1 again after reading the only word"
    for s in (tb.wptr, tb.rptr, tb.rq2, tb.wq2):
        assert u(s) == 1, f"{s._name} should be 1 (Gray code of 1) after one write and one read"
    tb.check()


@cocotb.test(timeout_time=20, timeout_unit="ms")
@cocotb.parametrize(clocks=list(CLOCKS))
async def test_fill_and_drain(dut, clocks):
    """Reader stopped: holding w_en high must be accepted exactly DEPTH times (not DEPTH-1),
    then w_full stays 1 and extra writes are ignored. Then read everything back in order."""
    tb = Tb(dut, *CLOCKS[clocks])
    await tb.start()
    dut.w_en.value = 1
    for i in range(DEPTH + 8):
        dut.w_data.value = random.randrange(1 << WIDTH)
        await RisingEdge(dut.wclk)
    dut.w_en.value = 0
    for _ in range(6):
        await RisingEdge(dut.wclk)
    await ReadOnly()
    assert tb.accepted == DEPTH, f"FIFO accepted {tb.accepted} writes with the reader stopped, expected DEPTH={DEPTH}"
    assert u(dut.w_full) == 1, "w_full should be 1 when the FIFO holds DEPTH entries"
    await RisingEdge(dut.rclk)
    dut.r_en.value = 1
    for _ in range(DEPTH + 12):
        await RisingEdge(dut.rclk)
    dut.r_en.value = 0
    for _ in range(8):
        await RisingEdge(dut.wclk)
    await ReadOnly()
    assert tb.popped == DEPTH, f"read back {tb.popped} entries, expected {DEPTH}"
    assert u(dut.r_empty) == 1 and u(dut.w_full) == 0, "FIFO should be empty and not full at the end"
    tb.check()


@cocotb.test(timeout_time=200, timeout_unit="ms")
@cocotb.parametrize(clocks=list(CLOCKS))
async def test_random_traffic(dut, clocks):
    """Randomized bursty writes/reads (including w_en while full and r_en while empty)
    with the scoreboard and Gray-pointer checks running on every edge."""
    tb = Tb(dut, *CLOCKS[clocks])
    await tb.start()
    await run_traffic(tb, scale(1500))
    tb.check()


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def test_empty_latency(dut):
    """A word written into an empty FIFO must become visible (r_empty=0) no earlier than the 2nd
    and no later than the 4th rclk rising edge after the wclk edge that wrote it."""
    tb = Tb(dut, 10000, 7000)
    await tb.start()
    lat, sync_lat = [], []
    for trial in range(12):
        await Timer(random.randrange(1, 10000), unit="ps")
        await RisingEdge(dut.wclk)
        dut.w_en.value = 1
        dut.w_data.value = random.randrange(1 << WIDTH)
        await RisingEdge(dut.wclk)       # the write happens on this edge
        t_w = get_sim_time(unit="ps")
        dut.w_en.value = 0
        target = None
        n = 0
        n_sync = None
        while True:
            await RisingEdge(dut.rclk)
            if get_sim_time(unit="ps") > t_w:
                n += 1
            await ReadOnly()
            if target is None:
                target = gray(tb.w_done % MOD)
            if n_sync is None and u(tb.rq2) == target:
                n_sync = n
            if not u(dut.r_empty):
                break
            assert n < 20, "written word never became visible on the read side"
        lat.append(n)
        sync_lat.append(n_sync)
        await RisingEdge(dut.rclk)
        dut.r_en.value = 1
        await RisingEdge(dut.rclk)
        dut.r_en.value = 0
        for _ in range(6):
            await RisingEdge(dut.rclk)
    dut._log.info(f"write->not-empty latency in rclk edges: {lat}; write->rq2_wptr_gray: {sync_lat}")
    assert None not in sync_lat and min(sync_lat) >= 2 and max(sync_lat) <= 3, (
        f"rq2_wptr_gray must be the output of a 2- (or 3-) flop synchronizer clocked by rclk, so it must pick "
        f"up a new write pointer on the 2nd (or 3rd) rclk edge after the write; seen: {sync_lat}")
    assert min(lat) >= 2, (f"r_empty fell only {min(lat)} rclk edge(s) after the write: the write pointer "
                           "must pass through a 2-flop synchronizer (latencies seen: {lat})")
    assert max(lat) <= 4, f"write->not-empty latency up to {max(lat)} rclk edges; spec allows at most 4 ({lat})"
    tb.check()


@cocotb.test(timeout_time=50, timeout_unit="ms")
async def test_full_latency(dut):
    """From full: after one read, w_full must drop no earlier than the 2nd and no later than the
    4th wclk rising edge after the rclk edge of the read."""
    tb = Tb(dut, 7000, 10000)
    await tb.start()
    lat, sync_lat = [], []
    for trial in range(8):
        dut.w_en.value = 1
        for _ in range(2 * DEPTH + 8):
            dut.w_data.value = random.randrange(1 << WIDTH)
            await RisingEdge(dut.wclk)
        dut.w_en.value = 0
        await ReadOnly()
        assert u(dut.w_full), "FIFO should be full"
        await Timer(random.randrange(1, 10000), unit="ps")
        await RisingEdge(dut.rclk)
        dut.r_en.value = 1
        await RisingEdge(dut.rclk)       # the read happens on this edge
        t_r = get_sim_time(unit="ps")
        dut.r_en.value = 0
        target = None
        n = 0
        n_sync = None
        while True:
            await RisingEdge(dut.wclk)
            if get_sim_time(unit="ps") > t_r:
                n += 1
            await ReadOnly()
            if target is None:
                target = gray(tb.r_done % MOD)
            if n_sync is None and u(tb.wq2) == target:
                n_sync = n
            if not u(dut.w_full):
                break
            assert n < 20, "w_full never dropped after a read"
        lat.append(n)
        sync_lat.append(n_sync)
        await RisingEdge(dut.wclk)
    dut._log.info(f"read->not-full latency in wclk edges: {lat}; read->wq2_rptr_gray: {sync_lat}")
    assert None not in sync_lat and min(sync_lat) >= 2 and max(sync_lat) <= 3, (
        f"wq2_rptr_gray must be the output of a 2- (or 3-) flop synchronizer clocked by wclk, so it must pick "
        f"up a new read pointer on the 2nd (or 3rd) wclk edge after the read; seen: {sync_lat}")
    assert min(lat) >= 2, (f"w_full fell only {min(lat)} wclk edge(s) after the read: the read pointer must "
                           f"pass through a 2-flop synchronizer (latencies seen: {lat})")
    assert max(lat) <= 4, f"read->not-full latency up to {max(lat)} wclk edges; spec allows at most 4 ({lat})"
    tb.check()


@cocotb.test(timeout_time=100, timeout_unit="ms")
async def test_reset_during_traffic(dut):
    """Reset both domains in the middle of traffic: afterwards the FIFO is empty (no stale data)
    and keeps working."""
    tb = Tb(dut, 6000, 9000)
    await tb.start()
    wd = cocotb.start_soon(tb.wdrive())
    rd = cocotb.start_soon(tb.rdrive())
    tb.p_w, tb.p_r, tb.w_budget = 0.8, 0.3, 10 ** 9
    await Timer(random.randrange(40, 80) * 9000 + random.randrange(9000), unit="ps")
    wd.cancel()
    rd.cancel()
    tb.accepted = tb.popped = 0
    await tb.reset()
    await run_traffic(tb, scale(400))
    tb.check()
