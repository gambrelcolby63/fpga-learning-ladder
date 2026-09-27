// LADDER-SKELETON: delete this line once you start implementing (see ../../README.md, "CI").
//
// 05-fir-filter / fir_filter : NTAPS-tap fixed-point FIR filter. Q1.15 samples and
// coefficients, full-precision accumulator, round-half-up + saturation to a Q1.15 output,
// AXI-Stream-style valid/ready on both sides, constant latency.
// The full spec is in ../README.md. The port list is fixed -- the testbench depends on it.
`default_nettype none

module fir_filter #(
    parameter int NTAPS = 16                  // number of taps, 2..64
) (
    input  logic                  clk,
    input  logic                  rst,        // synchronous, active-high
    // Coefficients, Q1.15 two's complement: h[k] = coefs[16*k +: 16], and h[0] multiplies the
    // NEWEST sample. Quasi-static: only changes while rst=1 (think "register bank").
    input  logic [16*NTAPS-1:0]   coefs,
    // Input samples, Q1.15. A sample is accepted on a rising edge with s_tvalid && s_tready.
    input  logic signed [15:0]    s_tdata,
    input  logic                  s_tvalid,
    output logic                  s_tready,
    // Output samples, Q1.15: y[n] = sat16((sum_k h[k]*x[n-k] + 2**14) >>> 15)
    output logic signed [15:0]    m_tdata,
    output logic                  m_tvalid,
    input  logic                  m_tready
);
    // Handy constants (see README section 1 for where they come from)
    localparam int GUARD = $clog2(NTAPS);     // guard bits: growth of a sum of NTAPS products
    localparam int ACC_W = 32 + GUARD;        // full-precision accumulator width (Q(2+GUARD).30)

    // TODO: implement. Delete the placeholder assignments below as you go.
    assign s_tready = 1'b1;
    assign m_tdata  = '0;
    assign m_tvalid = 1'b0;

endmodule

`default_nettype wire
