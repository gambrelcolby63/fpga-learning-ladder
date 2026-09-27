"""Python reference model / helpers for 8N1 UART frames."""
from __future__ import annotations


def clks_per_bit(clk_freq_hz: int, baud: int) -> int:
    """Same rounding the RTL must use: round to nearest."""
    return (clk_freq_hz + baud // 2) // baud


def frame_bits(byte: int, stop_val: int = 1) -> list[int]:
    """Line levels for one 8N1 frame: start(0), d0..d7 (LSB first), stop."""
    return [0] + [(byte >> i) & 1 for i in range(8)] + [stop_val]


def decode_tx_trace(tx: list[int], cpb: int, max_errors: int = 10):
    """Decode a per-clock trace of the tx line.

    Rules checked (see README): every bit lasts EXACTLY cpb clocks; start bit 0; stop bit 1;
    the line is high between frames.  Returns (frames, errors) where frames is a list of
    (start_index, byte).
    """
    frames: list[tuple[int, int]] = []
    errors: list[str] = []
    i = 1
    while i < len(tx) and len(errors) < max_errors:
        if tx[i - 1] == 1 and tx[i] == 0:
            start = i
            if start + 10 * cpb > len(tx):
                errors.append(f"frame starting at cycle {start} is incomplete at end of test")
                break
            bits = []
            for k in range(10):
                seg = tx[start + k * cpb:start + (k + 1) * cpb]
                if len(set(seg)) != 1:
                    name = "start" if k == 0 else "stop" if k == 9 else f"data{k - 1}"
                    errors.append(f"frame@cycle {start}: {name} bit is not a constant level for exactly "
                                  f"{cpb} clocks (levels={''.join(map(str, seg))})")
                bits.append(seg[cpb // 2])
            if bits[9] != 1:
                errors.append(f"frame@cycle {start}: stop bit is 0")
            byte = sum(b << j for j, b in enumerate(bits[1:9]))
            frames.append((start, byte))
            i = start + 10 * cpb
        else:
            i += 1
    return frames, errors
