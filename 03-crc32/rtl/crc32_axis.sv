// LADDER-SKELETON: delete this line once you start implementing (see ../../README.md, "CI").
//
// 03-crc32 / crc32_axis : Ethernet CRC-32 over a 64-bit AXI-Stream, one beat per clock.
// The full spec is in ../README.md. The port list is fixed -- the testbench depends on it.
`default_nettype none

module crc32_axis (
    input  logic        clk,
    input  logic        rst,          // synchronous, active-high
    // AXI-Stream input. Byte 0 of the frame is s_tdata[7:0]. s_tkeep is all ones except on the
    // last beat, where it is contiguous from bit 0 (8'b0000_0001 .. 8'b1111_1111).
    input  logic [63:0] s_tdata,
    input  logic [7:0]  s_tkeep,
    input  logic        s_tvalid,
    output logic        s_tready,     // must be 1 every cycle after reset (line rate, no stalls)
    input  logic        s_tlast,
    // one result per frame
    output logic        m_crc_valid,  // 1-cycle pulse, 0..3 cycles after the tlast beat
    output logic [31:0] m_crc,        // CRC-32 of every byte of the frame (== zlib.crc32(frame))
    output logic        m_crc_ok      // 1 if m_crc == 32'h2144DF1C (frame ends with a valid FCS)
);
    // Handy constants (see README for where they come from)
    localparam logic [31:0] POLY_REFLECTED = 32'hEDB88320;  // 0x04C11DB7 bit-reversed
    localparam logic [31:0] RESIDUE        = 32'h2144DF1C;

    // TODO: implement. Delete the placeholder assignments below as you go.
    assign s_tready    = 1'b1;
    assign m_crc_valid = 1'b0;
    assign m_crc       = '0;
    assign m_crc_ok    = 1'b0;

endmodule

`default_nettype wire
