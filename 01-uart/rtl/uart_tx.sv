// LADDER-SKELETON: delete this line once you start implementing (see ../../README.md, "CI").
//
// 01-uart / uart_tx : 8N1 UART transmitter. The full spec is in ../README.md.
// The port list below is fixed -- the testbench depends on it. Everything else is yours.
`default_nettype none

module uart_tx #(
    parameter int CLK_FREQ_HZ = 50_000_000,  // frequency of clk
    parameter int BAUD        = 115_200      // bits per second on the serial line
) (
    input  logic       clk,
    input  logic       rst,      // synchronous, active-high
    // byte input, valid/ready handshake: a byte is taken when s_valid && s_ready at a clk edge
    input  logic [7:0] s_data,
    input  logic       s_valid,
    output logic       s_ready,
    // serial output (idles high) and status
    output logic       tx,
    output logic       busy      // 1 while a frame is on the line
);
    // Clock cycles per bit, rounded to the nearest integer. Use this exact expression.
    localparam int CLKS_PER_BIT = (CLK_FREQ_HZ + BAUD / 2) / BAUD;

    // TODO: implement the transmitter.
    //   * pick a counter width from CLKS_PER_BIT ($clog2 is your friend)
    //   * a small FSM (IDLE / START / DATA / STOP) or a 10-bit shift register both work
    //   * delete these placeholder assignments when you drive the outputs yourself
    assign s_ready = 1'b0;
    assign tx      = 1'b1;
    assign busy    = 1'b0;

endmodule

`default_nettype wire
