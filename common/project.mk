# Shared Makefile logic for every project in the ladder. A project Makefile sets:
#
#   TESTS : list of  top:src1+src2:test_module:PARAM=V,PARAM=V   (one simulation per entry)
#   LINTS : list of  top:src1+src2:PARAM=V,PARAM=V               (one verilator -Wall run per entry)
#
# and then does `include ../common/project.mk`.
#
# Knobs (all optional):
#   make test SIM=icarus          use Icarus Verilog instead of Verilator
#   make test TOP=uart_rx         only run TESTS entries whose top is uart_rx
#   make test SEED=1234           reproduce a randomized run
#   make test TESTCASE=test_foo   run a single cocotb test function
#   make test RTL_DIR=some/dir    simulate RTL from another directory
#   make wave [TOP=...]           run the FIRST config of each top with FST waveform dumping
#   make wave WAVE_FMT=vcd        dump VCD instead (Verilator only; if FST support is missing)

SHELL       := /bin/bash
.SHELLFLAGS := -o pipefail -c

# cocotb 2.x needs Verilator >= 5.036. Prefer the one setup.sh builds into /opt if it exists.
VERILATOR_BIN_DIR ?= /opt/verilator-5.052/bin
ifneq ($(wildcard $(VERILATOR_BIN_DIR)/verilator),)
export PATH := $(VERILATOR_BIN_DIR):$(PATH)
endif

LADDER_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
PYTHON   ?= $(if $(wildcard $(LADDER_ROOT)/.venv/bin/python),$(LADDER_ROOT)/.venv/bin/python,python3)
RTL_DIR  ?= rtl
SIM      ?= verilator
TOP      ?=
VERILATOR ?= verilator
export RTL_DIR SIM SEED TESTCASE LADDER_SCALE WAVE_FMT

RUN := $(PYTHON) $(LADDER_ROOT)/common/run.py

.PHONY: test lint wave clean help

help:
	@echo "make test   - run the cocotb testbenches (all configs)"
	@echo "make lint   - verilator --lint-only -Wall on every top / parameter set"
	@echo "make wave   - run the first config of each top with FST dumping (open with gtkwave/surfer)"
	@echo "make clean"

test:
	@mkdir -p build; rm -f build/summary.txt; fail=0; \
	for t in $(TESTS); do \
	  IFS=: read -r top src mod params <<< "$$t"; \
	  if [ -n "$(TOP)" ] && [ "$$top" != "$(TOP)" ]; then continue; fi; \
	  echo "=== $$mod  top=$$top  $$params"; \
	  $(RUN) --top $$top --src $${src//+/,} --module $$mod --params "$$params" || fail=1; \
	done; \
	echo; echo "==================== $(notdir $(CURDIR)) summary ===================="; \
	cat build/summary.txt 2>/dev/null; \
	exit $$fail

lint:
	@$(VERILATOR) --version; fail=0; \
	for l in $(LINTS); do \
	  IFS=: read -r top src params <<< "$$l"; \
	  gargs=""; for p in $${params//,/ }; do gargs="$$gargs -G$$p"; done; \
	  files=""; for f in $${src//+/ }; do files="$$files $(RTL_DIR)/$$f"; done; \
	  echo "lint $$top $$params"; \
	  $(VERILATOR) --lint-only -Wall --top-module $$top $$gargs $$files || fail=1; \
	done; \
	if [ $$fail = 0 ]; then echo "LINT PASS"; else echo "LINT FAIL"; fi; exit $$fail

wave:
	@mkdir -p build; rm -f build/summary.txt; seen=" "; \
	for t in $(TESTS); do \
	  IFS=: read -r top src mod params <<< "$$t"; \
	  if [ -n "$(TOP)" ] && [ "$$top" != "$(TOP)" ]; then continue; fi; \
	  case "$$seen" in *" $$top "*) continue;; esac; seen="$$seen$$top "; \
	  $(RUN) --top $$top --src $${src//+/,} --module $$mod --params "$$params" --waves || true; \
	done; \
	echo; echo "Waveforms (open with: gtkwave <file>  or  surfer <file>):"; \
	find build \( -name '*.fst' -o -name '*.vcd' \) -mmin -10 | sort

clean:
	rm -rf build tb/__pycache__ results.xml
