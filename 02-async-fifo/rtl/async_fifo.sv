// LADDER-SKELETON: delete this line once you start implementing (see ../../README.md, "CI").
//
// 02-async-fifo / async_fifo : dual-clock FIFO, Gray-code pointers, 2-flop synchronizers.
// The full spec is in ../README.md. The port list is fixed (the testbench depends on it), and so
// are the NAMES of the four internal pointer registers listed below -- the testbench peeks at them.
`default_nettype none

module async_fifo #(
    parameter int WIDTH  = 8,  // bits per entry
    parameter int ADDR_W = 4   // DEPTH = 2**ADDR_W entries; ADDR_W >= 2
) (
    // ---- write clock domain ----
    input  logic             wclk,
    input  logic             wrst_n,   // active-low, asynchronous assert
    input  logic             w_en,     // write request; ignored while w_full = 1
    input  logic [WIDTH-1:0] w_data,
    output logic             w_full,
    // ---- read clock domain ----
    input  logic             rclk,
    input  logic             rrst_n,   // active-low, asynchronous assert
    input  logic             r_en,     // read (pop) request; ignored while r_empty = 1
    output logic [WIDTH-1:0] r_data,   // first-word-fall-through: valid whenever r_empty = 0
    output logic             r_empty
);
    localparam int DEPTH = 1 << ADDR_W;

    // REQUIRED internal registers (exact names; ADDR_W+1 bits each):
    //   wptr_gray     : write pointer, Gray coded, clocked by wclk
    //   rptr_gray     : read pointer, Gray coded, clocked by rclk
    //   rq2_wptr_gray : wptr_gray after the 2nd synchronizer flop in the rclk domain
    //   wq2_rptr_gray : rptr_gray after the 2nd synchronizer flop in the wclk domain
    logic [ADDR_W:0] wptr_gray, rptr_gray, rq2_wptr_gray, wq2_rptr_gray;

    // TODO: implement the FIFO. Delete the placeholder assignments below as you go.
    assign wptr_gray     = '0;
    assign rptr_gray     = '0;
    assign rq2_wptr_gray = '0;
    assign wq2_rptr_gray = '0;
    assign w_full  = 1'b0;
    assign r_data  = '0;
    assign r_empty = 1'b1;

endmodule

`default_nettype wire
