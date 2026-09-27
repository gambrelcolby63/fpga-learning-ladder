"""Bit-exact golden model for fir_filter (see ../README.md, section 2), written with numpy.

Everything here is plain integer arithmetic on the raw two's complement values, exactly what
the hardware does. A Q1.15 number is stored as an int16 `v` and means v / 2**15.

    acc[n] = sum_{k=0}^{NTAPS-1} h[k] * x[n-k]          (x[m] = 0 for m < 0; full precision)
    y[n]   = saturate16( floor((acc[n] + 2**14) / 2**15) )   (round half up, then saturate)

You can import this file in a Python shell to play with the numbers:

    >>> import fir_model as m
    >>> h = m.lowpass(16, 0.2); h[:4]
    >>> m.fir([32767] + [0] * 20, h)[:16]   # impulse response ~= h
"""
from __future__ import annotations

import numpy as np

W = 16                        # sample / coefficient / output width
FRAC = 15                     # Q1.15: 15 fractional bits
MAXV = (1 << (W - 1)) - 1     # +32767 = 0x7FFF  (+0.999969...)
MINV = -(1 << (W - 1))        # -32768 = 0x8000  (-1.0 exactly)


def guard_bits(ntaps: int) -> int:
    """Bits of growth when adding `ntaps` products: ceil(log2(ntaps))."""
    return max(0, (ntaps - 1).bit_length())


def acc_width(ntaps: int) -> int:
    """Full-precision accumulator width: a 16x16 signed product needs 32 bits, plus guard bits."""
    return 2 * W + guard_bits(ntaps)


# --------------------------------------------------------------------------------------------
# Quantization helpers (float <-> Q1.15)
# --------------------------------------------------------------------------------------------
def to_q15(x) -> np.ndarray:
    """Quantize real values in [-1, 1) to Q1.15 ints: round to nearest, clip to the int16 range.
    Clipping matters for +1.0, which is NOT representable (the largest value is 32767/32768)."""
    return np.clip(np.round(np.asarray(x, dtype=np.float64) * (1 << FRAC)), MINV, MAXV).astype(np.int64)


def from_q15(v) -> np.ndarray:
    return np.asarray(v, dtype=np.float64) / (1 << FRAC)


def lowpass(ntaps: int, cutoff: float = 0.2) -> list[int]:
    """Windowed-sinc (Hamming) low-pass filter quantized to Q1.15, DC gain ~= 1.0.
    `cutoff` is the -6 dB frequency as a fraction of the sample rate (0 < cutoff < 0.5).
    The result is symmetric (linear phase): h[k] == h[ntaps-1-k]."""
    n = np.arange(ntaps) - (ntaps - 1) / 2
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(ntaps) if ntaps > 1 else np.ones(1)
    h = h / np.sum(h)                          # normalize the DC gain to exactly 1.0 (in float)
    return [int(v) for v in to_q15(h)]


# --------------------------------------------------------------------------------------------
# The filter itself
# --------------------------------------------------------------------------------------------
def fir_acc(x, h) -> np.ndarray:
    """Full-precision accumulator value for every input sample (int64 never overflows here:
    |acc| <= 64 * 2**30 = 2**36)."""
    x = np.asarray(x, dtype=np.int64)
    h = np.asarray(h, dtype=np.int64)
    if len(x) == 0:
        return np.zeros(0, dtype=np.int64)
    return np.convolve(x, h)[: len(x)]      # causal: y[n] only uses x[0..n], history starts at 0


def round_sat(acc) -> np.ndarray:
    """Q(2+G).30 accumulator -> Q1.15 output: add half an output LSB (2**14), arithmetic shift
    right by 15 (numpy's >> on signed ints floors, like SystemVerilog's >>>), then clamp."""
    acc = np.asarray(acc, dtype=np.int64)
    return np.clip((acc + (1 << (FRAC - 1))) >> FRAC, MINV, MAXV)


def fir(x, h) -> list[int]:
    """Expected fir_filter output sequence for input samples x (ints) and coefficients h (ints)."""
    return [int(v) for v in round_sat(fir_acc(x, h))]


# --------------------------------------------------------------------------------------------
# Bit packing for the `coefs` port and 16-bit bus values
# --------------------------------------------------------------------------------------------
def pack_coefs(h) -> int:
    """coefs[16*k +: 16] = h[k] (two's complement)."""
    v = 0
    for k, c in enumerate(h):
        v |= (int(c) & 0xFFFF) << (W * k)
    return v


def s16(v: int) -> int:
    """Interpret a 16-bit bus value as signed."""
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v
