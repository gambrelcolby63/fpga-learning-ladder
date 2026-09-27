# 01-uart -- see README.md.   make test | make lint | make wave | make clean
#
# Each TESTS entry:  top:source(s):cocotb_test_module:PARAMS
# CLKS_PER_BIT for these configs: 16, 33, 87, 434
TESTS := \
  uart_tx:uart_tx.sv:test_uart_tx:CLK_FREQ_HZ=1843200,BAUD=115200 \
  uart_tx:uart_tx.sv:test_uart_tx:CLK_FREQ_HZ=100000000,BAUD=3000000 \
  uart_tx:uart_tx.sv:test_uart_tx:CLK_FREQ_HZ=10000000,BAUD=115200 \
  uart_tx:uart_tx.sv:test_uart_tx:CLK_FREQ_HZ=50000000,BAUD=115200 \
  uart_rx:uart_rx.sv:test_uart_rx:CLK_FREQ_HZ=1843200,BAUD=115200 \
  uart_rx:uart_rx.sv:test_uart_rx:CLK_FREQ_HZ=100000000,BAUD=3000000 \
  uart_rx:uart_rx.sv:test_uart_rx:CLK_FREQ_HZ=10000000,BAUD=115200

LINTS := \
  uart_tx:uart_tx.sv:CLK_FREQ_HZ=1843200,BAUD=115200 \
  uart_tx:uart_tx.sv:CLK_FREQ_HZ=50000000,BAUD=115200 \
  uart_rx:uart_rx.sv:CLK_FREQ_HZ=1843200,BAUD=115200 \
  uart_rx:uart_rx.sv:CLK_FREQ_HZ=50000000,BAUD=115200

include ../common/project.mk
