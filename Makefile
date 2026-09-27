# Top-level convenience targets. Each project also has its own Makefile (cd into it to work).
#   make test     run every project's tests (keeps going after failures) and print a summary
#   make lint     lint every project
#   make status   lint + test per project, CI-style, one line each
#   make clean
PROJECTS := 01-uart 02-async-fifo 03-crc32 04-eth-parser 05-fir-filter

.PHONY: test lint status clean
test:
	@for p in $(PROJECTS); do $(MAKE) -s -C $$p test > /dev/null 2>&1; done; \
	for p in $(PROJECTS); do echo "--- $$p"; cat $$p/build/summary.txt 2>/dev/null || echo "(no results)"; done

lint:
	@fail=0; for p in $(PROJECTS); do echo "--- $$p"; $(MAKE) -s -C $$p lint 2>&1 | grep -E "%(Warning|Error)|LINT" || true; done

status:
	@for p in $(PROJECTS); do scripts/ci_project.sh $$p | head -n 1; done

clean:
	@for p in $(PROJECTS); do $(MAKE) -s -C $$p clean; done
