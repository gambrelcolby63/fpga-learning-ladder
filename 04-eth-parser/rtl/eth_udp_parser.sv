// LADDER-SKELETON: delete this line once you start implementing (see ../../README.md, "CI").
//
// 04-eth-parser / eth_udp_parser : Ethernet II -> IPv4 -> UDP header parser with a realigned
// payload stream. The full spec is in ../README.md. The port list is fixed -- the testbench
// depends on it.
`default_nettype none

module eth_udp_parser (
    input  logic        clk,
    input  logic        rst,             // synchronous, active-high
    // ---- input: one Ethernet frame per packet, dst MAC first, FCS already removed ----
    // byte 0 of the frame is s_tdata[7:0]; s_tkeep is all ones except on the last beat
    input  logic [63:0] s_tdata,
    input  logic [7:0]  s_tkeep,
    input  logic        s_tvalid,
    output logic        s_tready,
    input  logic        s_tlast,
    // ---- output 1: one header record per forwarded (IPv4/UDP) frame ----
    // multi-byte fields in network byte order: the first byte on the wire is the MSB
    output logic        m_hdr_valid,
    input  logic        m_hdr_ready,
    output logic [47:0] m_dst_mac,
    output logic [47:0] m_src_mac,
    output logic [15:0] m_ethertype,     // always 16'h0800 for forwarded frames
    output logic [31:0] m_ip_src,
    output logic [31:0] m_ip_dst,
    output logic [15:0] m_udp_src_port,
    output logic [15:0] m_udp_dst_port,
    output logic [15:0] m_udp_len,       // UDP length field (8-byte UDP header + payload)
    // ---- output 2: the UDP payload, realigned so payload byte 0 is m_tdata[7:0] ----
    output logic [63:0] m_tdata,
    output logic [7:0]  m_tkeep,
    output logic        m_tvalid,
    input  logic        m_tready,
    output logic        m_tlast
);

    // TODO: implement. Delete the placeholder assignments below as you go.
    assign s_tready       = 1'b1;   // placeholder: swallow everything
    assign m_hdr_valid    = 1'b0;
    assign m_dst_mac      = '0;
    assign m_src_mac      = '0;
    assign m_ethertype    = '0;
    assign m_ip_src       = '0;
    assign m_ip_dst       = '0;
    assign m_udp_src_port = '0;
    assign m_udp_dst_port = '0;
    assign m_udp_len      = '0;
    assign m_tdata        = '0;
    assign m_tkeep        = '0;
    assign m_tvalid       = 1'b0;
    assign m_tlast        = 1'b0;

endmodule

`default_nettype wire
