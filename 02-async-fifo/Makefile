# 02-async-fifo -- see README.md.   make test | make lint | make wave | make clean
#
# Each TESTS entry:  top:source(s):cocotb_test_module:PARAMS
TESTS := \
  async_fifo:async_fifo.sv:test_async_fifo:WIDTH=8,ADDR_W=4 \
  async_fifo:async_fifo.sv:test_async_fifo:WIDTH=32,ADDR_W=2 \
  async_fifo:async_fifo.sv:test_async_fifo:WIDTH=12,ADDR_W=6

LINTS := \
  async_fifo:async_fifo.sv:WIDTH=8,ADDR_W=4 \
  async_fifo:async_fifo.sv:WIDTH=32,ADDR_W=2 \
  async_fifo:async_fifo.sv:WIDTH=12,ADDR_W=6

include ../common/project.mk
