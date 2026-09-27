"""cocotb testbench for fir_filter (see ../README.md for the spec it checks).

Every output sample is compared bit-for-bit with the numpy golden model in fir_model.py.
The driver/monitor below also checks the valid/ready rules, the reset behavior and the latency
rule (constant latency L, 0 <= L <= NTAPS + 8 cycles, measured whenever m_tready is held at 1).

Latency convention: if a sample is accepted at rising edge number n (s_tvalid && s_tready just
before edge n), and its result is first presented (m_tvalid=1) in the clock cycle that starts
at edge n+L, the latency is L. L = 0 means the output register is loaded by the same edge that
accepts the sample.
"""
from __future__ import annotations

import math
import random
from collections import deque
from typing import Callable

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

from fir_model import MAXV, MINV, fir, fir_acc, lowpass, pack_coefs, round_sat, s16, to_q15
from ladder_tb import param, resolvable, scale, u

NTAPS = param("NTAPS", 16)
MAX_LAT = NTAPS + 8


def rand_coefs(n: int = NTAPS) -> list[int]:
    return [random.randint(MINV, MAXV) for _ in range(n)]


def rand_samples(n: int, p_extreme: float = 0.1) -> list[int]:
    """Full-range random samples, with the two extremes (and their neighbours) over-represented."""
    out = []
    for _ in range(n):
        if random.random() < p_extreme:
            out.append(random.choice([MAXV, MINV, MAXV - 1, MINV + 1, 0, 1, -1]))
        else:
            out.append(random.randint(MINV, MAXV))
    return out


def taps(*head: int) -> list[int]:
    """Coefficient list that starts with `head` and is zero-padded to NTAPS."""
    h = list(head[:NTAPS])
    return h + [0] * (NTAPS - len(h))


class Tb:
    def __init__(self, dut, h: list[int], p_idle: float = 0.0, p_ready: float = 1.0,
                 ready_fn: Callable[[int], bool] | None = None):
        self.dut = dut
        self.h = list(h)
        self.p_idle = p_idle
        self.p_ready = p_ready
        self.ready_fn = ready_fn
        dut.rst.value = 1
        dut.s_tvalid.value = 0
        dut.s_tdata.value = 0
        dut.m_tready.value = 0
        dut.coefs.value = pack_coefs(self.h)
        Clock(dut.clk, 4, unit="ns").start()
        self.queue: deque[int] = deque()
        self.cur: int | None = None
        self.edge = 0
        self.monitoring = False
        self.errors: list[str] = []
        self._clear()
        cocotb.start_soon(self._run())

    def _clear(self):
        self.sent: list[int] = []          # samples accepted by the DUT, in order
        self.acc_edge: list[int] = []      # edge number that accepted each sample
        self.got: list[int] = []           # output samples accepted from the DUT, in order
        self.out_edge: list[int] = []      # cycle in which each output was first presented
        self.stalled: int | None = None
        self.first_edge = 0

    def err(self, msg: str):
        if len(self.errors) < 20:
            self.errors.append(f"cycle {self.edge}: {msg}")

    def m_ready_now(self) -> bool:
        if self.ready_fn is not None:
            return self.ready_fn(self.edge)
        return random.random() < self.p_ready

    async def _run(self):
        dut = self.dut
        prev_rst = False
        while True:
            await RisingEdge(dut.clk)
            self.edge += 1
            # ---- drive (right after the edge) ----
            if not self.monitoring:
                self.cur = None
            elif self.cur is None and self.queue and random.random() >= self.p_idle:
                self.cur = self.queue.popleft()
            if self.cur is None:
                dut.s_tvalid.value = 0
                dut.s_tdata.value = random.getrandbits(16)      # junk while s_tvalid=0
            else:
                dut.s_tvalid.value = 1
                dut.s_tdata.value = self.cur & 0xFFFF
            r = self.m_ready_now()
            dut.m_tready.value = int(r)
            # ---- sample (everything settled) ----
            await ReadOnly()
            rst_now = bool(u(dut.rst))
            if prev_rst:
                # the edge that started this cycle saw rst=1: synchronous reset has taken effect
                if not resolvable(dut.m_tvalid):
                    self.err("m_tvalid is X/Z after a reset edge")
                elif u(dut.m_tvalid):
                    self.err("m_tvalid=1 right after a reset edge (reset must clear the output)")
            prev_rst = rst_now
            if rst_now or not self.monitoring:
                self.stalled = None
                continue
            if not resolvable(dut.s_tready) or not resolvable(dut.m_tvalid):
                self.err("s_tready or m_tvalid is X/Z after reset")
                continue
            s_rdy = u(dut.s_tready)
            if self.ready_fn is None and self.p_ready >= 1.0 and not s_rdy:
                self.err("s_tready=0 although m_tready has been held at 1 "
                         "(the filter must accept one sample per clock when not backpressured)")
            # input handshake (happens at the next edge)
            if self.cur is not None and s_rdy:
                self.sent.append(self.cur)
                self.acc_edge.append(self.edge + 1)
                self.cur = None
            # output
            if u(dut.m_tvalid):
                if not resolvable(dut.m_tdata):
                    self.err("m_tdata is X/Z while m_tvalid=1")
                    continue
                d = s16(u(dut.m_tdata))
                if self.stalled is not None:
                    if d != self.stalled:
                        self.err(f"m_tdata changed from {self.stalled} to {d} while m_tvalid=1 and m_tready=0")
                else:
                    self.first_edge = self.edge
                if r:
                    self.got.append(d)
                    self.out_edge.append(self.first_edge)
                    self.stalled = None
                else:
                    self.stalled = d
            elif self.stalled is not None:
                self.err("m_tvalid dropped before the output sample was accepted")
                self.stalled = None

    async def cycles(self, n: int):
        for _ in range(n):
            await RisingEdge(self.dut.clk)

    async def restart(self, h: list[int] | None = None, cycles: int = 3):
        """Assert reset (optionally loading new coefficients while in reset), then release it."""
        self.monitoring = False
        self.queue.clear()
        self.dut.rst.value = 1
        if h is not None:
            self.h = list(h)
            self.dut.coefs.value = pack_coefs(self.h)
        await self.cycles(cycles)
        self.dut.rst.value = 0
        await self.cycles(1)
        self._clear()
        self.monitoring = True

    async def run(self, samples: list[int], max_quiet: int = 400):
        """Send samples and wait until every output has come out (plus a few extra cycles, so
        that spurious extra outputs are caught)."""
        start = len(self.sent)
        self.queue.extend(samples)
        quiet = 0
        last = -1
        while self.queue or self.cur is not None or len(self.got) < len(self.sent):
            await RisingEdge(self.dut.clk)
            progress = len(self.sent) + len(self.got)
            quiet = 0 if progress != last else quiet + 1
            last = progress
            if quiet > max_quiet:
                break
        await self.cycles(MAX_LAT + 4)
        assert self.sent[start:] == list(samples)[: len(self.sent) - start], \
            "testbench bug: accepted samples differ from the samples sent"

    def check(self, check_latency: bool = True, what: str = ""):
        tag = f"[{what}] " if what else ""
        assert not self.errors, tag + "protocol errors:\n  " + "\n  ".join(self.errors)
        assert not self.queue and self.cur is None, (
            f"{tag}{len(self.queue) + (self.cur is not None)} input samples were never accepted (s_tready stuck at 0?)")
        exp = fir(self.sent, self.h)
        n = min(len(exp), len(self.got))
        for i in range(n):
            if exp[i] != self.got[i]:
                raise AssertionError(tag + self._diagnose(i, exp))
        assert len(self.got) == len(exp), (
            f"{tag}{len(exp)} input samples accepted but {len(self.got)} output samples came out"
            + (" (outputs missing: does the last sample only come out when another one pushes it?)"
               if len(self.got) < len(exp) else " (extra outputs)"))
        if check_latency and self.got:
            lats = [o - a for o, a in zip(self.out_edge, self.acc_edge)]
            lat0 = lats[0]
            assert 0 <= lat0 <= MAX_LAT, (
                f"{tag}latency {lat0} cycles is outside the allowed 0..{MAX_LAT} (NTAPS+8)")
            bad = [(i, l) for i, l in enumerate(lats) if l != lat0]
            assert not bad, (f"{tag}latency is not constant: sample 0 took {lat0} cycles, sample {bad[0][0]} "
                             f"took {bad[0][1]} (with m_tready=1 every sample must take the same L cycles)")
            return lat0
        return None

    def _diagnose(self, i: int, exp: list[int]) -> str:
        acc = int(fir_acc(self.sent, self.h)[i])
        g, e = self.got[i], exp[i]
        lines = [f"output sample #{i}: got {g}, expected {e}  (full-precision accumulator = {acc}, "
                 f"acc/2**15 = {acc / 32768:.4f})"]
        lo = max(0, i - 3)
        lines.append("   idx   input  expected       got")
        for j in range(lo, min(len(self.got), len(exp), i + 4)):
            mark = " <--" if j == i else ""
            lines.append(f"  {j:4d} {self.sent[j]:7d} {exp[j]:9d} {self.got[j]:9d}{mark}")
        if abs(g - e) == 1:
            lines.append("hint: off by one LSB -> check the rounding (add 2**14, then an ARITHMETIC shift by 15)")
        elif e in (MAXV, MINV) and (g < 0) != (e < 0):
            lines.append("hint: the result wrapped around instead of saturating (or the accumulator is too narrow)")
        elif i > 0 and g == exp[i - 1]:
            lines.append("hint: you output the previous sample's result: valid and data are misaligned by a cycle")
        elif i + 1 < len(exp) and g == exp[i + 1]:
            lines.append("hint: you output the next sample's result: valid and data are misaligned by a cycle")
        else:
            lines.append(f"hint: coefficients h = {self.h}; compare taps/order/sign extension with the README")
        return "\n".join(lines)


# ============================================================================================
# Tests
# ============================================================================================
@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_impulse_response(dut):
    """An impulse of +0x7FFF (~ +1.0) makes the output walk through the coefficients h[0], h[1], ...
    (within 1 LSB, since 0x7FFF is 1 - 2**-15), and an impulse of 0x8000 (-1.0 exactly) gives -h
    exactly. Tried with ascending coefficients (tap order is obvious), a low-pass and random taps."""
    sets = {
        "ascending": [(k + 1) * (30000 // NTAPS) * (1 if k % 2 == 0 else -1) for k in range(NTAPS)],
        "lowpass": lowpass(NTAPS, 0.2),
        "random": rand_coefs(),
    }
    tb = Tb(dut, sets["ascending"])
    for name, h in sets.items():
        await tb.restart(h)
        pad = NTAPS + 4
        x = [0] * 3 + [MAXV] + [0] * pad + [MINV] + [0] * pad
        await tb.run(x)
        tb.check(what=f"impulse, {name} taps")
        resp_pos = tb.got[3:3 + NTAPS]
        resp_neg = tb.got[4 + pad:4 + pad + NTAPS]
        for k in range(NTAPS):
            assert abs(resp_pos[k] - h[k]) <= 1, f"{name}: impulse response[{k}] = {resp_pos[k]}, h[{k}] = {h[k]}"
            assert resp_neg[k] == min(MAXV, -h[k]), f"{name}: -1.0 impulse response[{k}] = {resp_neg[k]}, -h[{k}] = {-h[k]}"
        dut._log.info(f"{name}: impulse response OK: {resp_pos[:8]}{' ...' if NTAPS > 8 else ''}")


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_step_response(dut):
    """Steps of +0.5, -0.5, +full scale and -full scale into a low-pass filter. The output settles
    at DC gain x step. The quantized low-pass has a DC gain slightly different from 1.0, so the
    full-scale steps may need saturation."""
    h = lowpass(NTAPS, 0.2)
    tb = Tb(dut, h)
    await tb.restart(h)
    L = NTAPS + 6
    x = []
    for a in (0x4000, -0x4000, MAXV, MINV):
        x += [a] * L + [0] * L
    await tb.run(x)
    tb.check(what="step")
    for j, a in enumerate((0x4000, -0x4000, MAXV, MINV)):
        settled = tb.got[2 * j * L + L - 1]
        want = int(round_sat(sum(h) * a))
        assert settled == want, f"step {a}: settled output {settled}, expected DC gain * step = {want}"
    dut._log.info(f"DC gain of the quantized low-pass = sum(h)/32768 = {sum(h) / 32768:.6f}")


@cocotb.test(timeout_time=30, timeout_unit="ms")
async def test_random_bitexact(dut):
    """Random full-scale samples through four coefficient sets, compared bit-exact with the model:
    random full-range taps (lots of saturation), small taps with sum|h| < 1 (no saturation, all
    rounding), a low-pass, and a sparse set (mostly zeros)."""
    small = [random.randint(-(32768 // NTAPS), 32768 // NTAPS - 1) for _ in range(NTAPS)]
    sparse = [random.choice([0, 0, 0, random.randint(MINV, MAXV)]) for _ in range(NTAPS)]
    sets = {"random": rand_coefs(), "small": small, "lowpass": lowpass(NTAPS, 0.15), "sparse": sparse}
    tb = Tb(dut, sets["random"])
    for name, h in sets.items():
        await tb.restart(h)
        await tb.run(rand_samples(scale(1200)))
        tb.check(what=f"random samples, {name} taps")
        sat = sum(1 for v in tb.got if v in (MAXV, MINV))
        dut._log.info(f"{name}: {len(tb.got)} samples bit-exact ({sat} saturated)")


@cocotb.test(timeout_time=5, timeout_unit="ms")
async def test_rounding(dut):
    """Exact ties (x.5 LSB) and near-ties, positive and negative. Round half UP means
    +0.5 -> +1 and -0.5 -> 0, +1.5 -> 2, -1.5 -> -1. Truncation, round-half-away-from-zero and
    round-half-even all fail here."""
    tb = Tb(dut, taps(0x4000))
    # h[0] = 0.5: y = round(x/2). Odd x -> exact ties.
    await tb.restart(taps(0x4000))
    x = list(range(-9, 10)) + [MAXV, MINV, MINV + 1, MAXV - 1, 12345, -12345]
    await tb.run(x)
    tb.check(what="h[0]=0.5 ties")
    assert tb.got[x.index(1)] == 1 and tb.got[x.index(-1)] == 0, \
        f"round(+0.5) should be 1 and round(-0.5) should be 0, got {tb.got[x.index(1)]} and {tb.got[x.index(-1)]}"
    # h = [1, 1, ...]: acc = sum of the last NTAPS samples, output = round(acc / 32768).
    await tb.restart([1] * NTAPS)
    x = []
    for target in (16384, -16384, 16383, -16383, 16385, -16385, 49152, -49152, 3 * 16384, -3 * 16384):
        # spread `target` over NTAPS samples, then flush with zeros
        q, r = divmod(abs(target), NTAPS)
        seq = [q + (1 if k < r else 0) for k in range(NTAPS)]
        seq = [v if target > 0 else -v for v in seq]
        x += seq + [0] * NTAPS
    await tb.run(x)
    tb.check(what="h=[1,...] ties")
    # random small coefficients, small samples: most outputs are pure rounding decisions
    h = [random.randint(-64, 64) for _ in range(NTAPS)]
    await tb.restart(h)
    await tb.run([random.randint(-3000, 3000) for _ in range(scale(600))])
    tb.check(what="small values")


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def test_saturation(dut):
    """Max/min corner cases: all taps and samples at +max / -1.0 (the largest possible
    accumulator is NTAPS * (-1.0 * -1.0) = NTAPS * 2**30, which needs every guard bit), the
    single-product corner -1.0 * -1.0 = +1.0 (needs a 32-bit product), worst-case sign patterns,
    and values right at the edge of the output range (one LSB inside and outside)."""
    cases = {
        "all +max taps, +max samples": ([MAXV] * NTAPS, [MAXV] * (2 * NTAPS)),
        "all +max taps, -1.0 samples": ([MAXV] * NTAPS, [MINV] * (2 * NTAPS)),
        "all -1.0 taps, -1.0 samples": ([MINV] * NTAPS, [MINV] * (2 * NTAPS)),
        "all -1.0 taps, +max samples": ([MINV] * NTAPS, [MAXV] * (2 * NTAPS)),
        "single tap -1.0 * -1.0": (taps(MINV), [MINV, 0, MINV, MAXV, MINV, MINV, 0]),
    }
    lp = lowpass(NTAPS, 0.2)
    worst = [MAXV if c >= 0 else MINV for c in reversed(lp)]  # x[n-k] has the sign of h[k]
    cases["low-pass, worst-case sign pattern"] = (lp, worst + [-v if v != MINV else MAXV for v in worst] + worst)
    edge = []
    for x2 in (16383, 16384, 16385, 32767, -16383, -16384, -16385, -32768, 0):
        edge += [x2, MAXV, MAXV, 0, 0, 0, x2, MINV, MINV, 0, 0, 0]
    # y[n] = 0.5*x[n] + 0.5*x[n-1] + x[n-2]/32768 lands exactly on the output-range edges
    cases["edge of range: h=[0.5, 0.5, 1 LSB]"] = (taps(0x4000, 0x4000, 1), edge)
    cases["random full-scale +/-max"] = (rand_coefs(), [random.choice([MAXV, MINV]) for _ in range(scale(400))])
    tb = Tb(dut, cases["single tap -1.0 * -1.0"][0])
    for name, (h, x) in cases.items():
        await tb.restart(h)
        await tb.run(x)
        tb.check(what=name)


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def test_lowpass_tones(dut):
    """A realistic use: a low-frequency tone plus a high-frequency 'interferer' through a numpy-
    designed low-pass. Bit-exact check, plus the measured attenuation is logged."""
    h = lowpass(NTAPS, 0.1)
    tb = Tb(dut, h)
    await tb.restart(h)
    n = np.arange(scale(800))
    f_lo, f_hi = 0.02, 0.4
    x = [int(v) for v in to_q15(0.45 * np.sin(2 * np.pi * f_lo * n) + 0.45 * np.sin(2 * np.pi * f_hi * n + 1.0))]
    await tb.run(x)
    tb.check(what="two tones")

    def tone_db(sig, f):
        s = np.asarray(sig[3 * NTAPS:], dtype=float)
        k = np.arange(len(s))
        return 20 * math.log10(max(1e-9, abs(np.sum(s * np.exp(-2j * np.pi * f * k))) * 2 / len(s) / 32768))
    dut._log.info(f"tone at {f_lo} fs: {tone_db(x, f_lo):.1f} dBFS in -> {tone_db(tb.got, f_lo):.1f} dBFS out; "
                  f"tone at {f_hi} fs: {tone_db(x, f_hi):.1f} dBFS in -> {tone_db(tb.got, f_hi):.1f} dBFS out")


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def test_latency_and_throughput(dut):
    """With m_tready held at 1: one sample per clock (s_tready always 1), the same latency L for
    every sample, 0 <= L <= NTAPS+8, and an isolated sample comes out after L cycles WITHOUT
    needing later samples to push it through. Then the same with random gaps in s_tvalid."""
    h = rand_coefs()
    tb = Tb(dut, h)
    await tb.restart(h)
    await tb.run(rand_samples(scale(300)))
    L = tb.check(what="full-rate stream")
    dut._log.info(f"measured latency L = {L} cycles (allowed 0..{MAX_LAT})")
    # isolated samples: nothing follows them
    await tb.restart(h)
    for i in range(6):
        tb.queue.append(random.randint(MINV, MAXV))
        while tb.queue or tb.cur is not None:
            await RisingEdge(dut.clk)
        await tb.cycles(MAX_LAT + 2)
        assert len(tb.got) == len(tb.sent), (
            f"isolated sample #{i}: no output {MAX_LAT + 2} cycles after it was accepted. The pipeline "
            "must keep moving when no new sample arrives (don't advance it only on s_tvalid)")
    assert tb.check(what="isolated samples") == L, "latency for isolated samples differs from the full-rate latency"
    # random gaps between samples
    tb.p_idle = 0.5
    await tb.restart(h)
    await tb.run(rand_samples(scale(300)))
    assert tb.check(what="input gaps") == L, "latency with input gaps differs from the full-rate latency"


@cocotb.test(timeout_time=40, timeout_unit="ms")
async def test_backpressure(dut):
    """Random gaps in s_tvalid AND random m_tready, then long m_tready=0 stretches: no sample may
    be lost, duplicated or reordered; m_tdata must hold while stalled; results stay bit-exact."""
    h = rand_coefs()
    tb = Tb(dut, h, p_idle=0.3, p_ready=0.5)
    await tb.restart(h)
    await tb.run(rand_samples(scale(1000)))
    tb.check(check_latency=False, what="random backpressure")
    tb.ready_fn = lambda e: (e // 23) % 4 == 3 or ((e // 23) % 4 == 2 and random.random() < 0.5)
    tb.p_idle = 0.0
    await tb.restart(lowpass(NTAPS, 0.25))
    await tb.run(rand_samples(scale(800)))
    tb.check(check_latency=False, what="long m_tready=0 stretches")


@cocotb.test(timeout_time=10, timeout_unit="ms")
async def test_reset(dut):
    """Reset in the middle of a stream (pipeline full, output stalled): m_tvalid goes low, nothing
    from before the reset ever comes out, and the filter restarts with an all-zero history."""
    h = rand_coefs()
    tb = Tb(dut, h, p_ready=0.7)
    await tb.restart(h)
    for trial in range(4):
        tb.queue.extend(rand_samples(200, p_extreme=0.5))
        await tb.cycles(random.randint(NTAPS, 3 * NTAPS))
        if trial % 2 == 0:
            tb.ready_fn = lambda e: False          # output stuck: a result is waiting at reset time
            await tb.cycles(MAX_LAT + 2)
        tb.ready_fn = None
        await tb.restart(rand_coefs() if trial == 3 else None, cycles=1 + trial)
        x = rand_samples(3 * NTAPS, p_extreme=0.5)
        await tb.run(x)
        tb.check(check_latency=False, what=f"after reset #{trial}")
        assert tb.sent == x
