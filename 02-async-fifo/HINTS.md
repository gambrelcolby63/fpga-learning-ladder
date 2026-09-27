# 02-async-fifo hints

One level at a time; 30+ minutes stuck before opening the next.

<details><summary><b>Level 1: block diagram</b></summary>

Draw it before writing code. You need:

* a memory array `logic [WIDTH-1:0] mem [DEPTH]`, written on `wclk`;
* write-domain: binary pointer, Gray pointer (`wptr_gray`), 2-flop synchronizer bringing
  `rptr_gray` in (`wq1_…` → `wq2_rptr_gray`), full flag;
* read-domain: the mirror image, plus `r_data` read from `mem` at the read address.

Everything in the write domain is clocked by `wclk` only; everything in the read domain by `rclk`
only. The *only* signals that cross are `wptr_gray` → read domain and `rptr_gray` → write domain
(plus the memory, which you write in one domain and read in the other).
</details>

<details><summary><b>Level 2: pointer arithmetic</b></summary>

* Compute `wbin_next = wbin + (w_en && !w_full)` and `wgray_next = gray(wbin_next)`
  combinationally, then register both. That way `wptr_gray` is a flip-flop output (clean for
  the synchronizer) and you can use the *next* values for the flags.
* The RAM address is the low `ADDR_W` bits of the **binary** pointer.
* First-word fall-through: `r_data` is simply the memory at the current read address
  (combinational read, which becomes distributed RAM/LUTRAM on an FPGA).
</details>

<details><summary><b>Level 3: flags</b></summary>

* Registered empty: `r_empty <= (rgray_next == rq2_wptr_gray)`. Why the *next* read pointer?
  Because if this cycle's read empties the FIFO, the flag must already be 1 in the next cycle.
* Registered full: compare `wgray_next` with `wq2_rptr_gray` with its **top two bits inverted**.
  A constant mask with bits `ADDR_W` and `ADDR_W-1` set, XORed onto `wq2_rptr_gray`, is a clean
  way to write it.
* Reset values: empty = 1, full = 0.
</details>

<details><summary><b>Level 4: debugging failing tests</b></summary>

* "accepted DEPTH-1 writes": your full condition fires one entry early, probably by comparing
  without the extra MSB, or comparing the current (not next) pointer in a way that fires early.
* "r_empty=0 but the FIFO holds no completed write": your empty flag uses the *current* read
  pointer and misses the read happening this cycle.
* "wptr_gray=… but N writes have completed": the Gray register isn't updated on the write edge,
  or isn't the Gray code of the binary count.
* Latency "1 rclk edge": a synchronizer flop is missing. Latency 5+: one too many registers.
* Use `make wave`, put both clocks and all pointers in the viewer, and step to the first error
  time printed by the scoreboard.
</details>
