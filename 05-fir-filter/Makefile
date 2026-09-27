# 05-fir-filter -- see README.md.   make test | make lint | make wave | make clean
#
# Each TESTS entry:  top:source(s):cocotb_test_module:PARAMS
# NTAPS=16 is the default; 5 (odd, not a power of two) and 32 (a deeper adder tree) catch
# designs that only work for one size.
TESTS := \
  fir_filter:fir_filter.sv:test_fir_filter:NTAPS=16 \
  fir_filter:fir_filter.sv:test_fir_filter:NTAPS=5 \
  fir_filter:fir_filter.sv:test_fir_filter:NTAPS=32

LINTS := \
  fir_filter:fir_filter.sv:NTAPS=16 \
  fir_filter:fir_filter.sv:NTAPS=5 \
  fir_filter:fir_filter.sv:NTAPS=32

include ../common/project.mk
