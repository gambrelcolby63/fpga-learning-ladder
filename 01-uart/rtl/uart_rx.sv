// LADDER-SKELETON: delete this line once you start implementing (see ../../README.md, "CI").
//
// 01-uart / uart_rx : 8N1 UART receiver. The full spec is in ../README.md.
// The port list below is fixed -- the testbench depends on it. Everything else is yours.
`default_nettype none

module uart_rx #(
    parameter int CLK_FREQ_HZ = 50_000_000,
    parameter int BAUD        = 115_200
) (
    input  logic       clk,
    input  logic       rst,        // synchronous, active-high
    input  logic       rx,         // serial input -- ASYNCHRONOUS to clk (synchronize it!)
    output logic [7:0] m_data,     // received byte, only meaningful while m_valid = 1
    output logic       m_valid,    // 1-clock pulse per correctly framed byte
    output logic       frame_err   // 1-clock pulse when the stop bit is sampled as 0
);
    localparam int CLKS_PER_BIT = (CLK_FREQ_HZ + BAUD / 2) / BAUD;

    // TODO: implement the receiver.
    //   1. two-flop synchronizer on rx
    //   2. detect the falling edge of the start bit
    //   3. re-check the line in the MIDDLE of the start bit (reject glitches)
    //   4. sample 8 data bits (LSB first) in the middle of each bit
    //   5. sample the stop bit: 1 -> m_valid pulse, 0 -> frame_err pulse (and wait for rx=1)
    assign m_data    = 8'h00;
    assign m_valid   = 1'b0;
    assign frame_err = 1'b0;

endmodule

`default_nettype wire
