#!/usr/bin/env bash
# One-time toolchain setup for Ubuntu 22.04/24.04, Debian 12/13, and WSL2 running one of those.
#
#   ./setup.sh               apt packages + Verilator 5.052 (built from source if needed) + .venv
#   SKIP_VERILATOR=1 ./setup.sh   skip the Verilator build (e.g. you already have >= 5.036)
#
# Why build Verilator? cocotb 2.x needs Verilator >= 5.036, and the distro packages are older
# (Ubuntu 24.04 ships 5.020, Debian 13 ships 5.032). The build takes ~5-15 minutes and installs
# into /opt/verilator-5.052, which the project Makefiles pick up automatically.
set -euo pipefail
VLT_VER=${VLT_VER:-v5.052}
PREFIX=/opt/verilator-${VLT_VER#v}
cd "$(dirname "$0")"

echo "==> apt packages"
sudo apt-get update
sudo apt-get install -y git make autoconf g++ flex bison libfl2 libfl-dev help2man perl zlib1g-dev ccache \
    python3 python3-venv python3-dev liblz4-dev iverilog yosys gtkwave

have_ok_verilator() {
  local v
  for exe in "$PREFIX/bin/verilator" "$(command -v verilator || true)"; do
    [ -n "$exe" ] && [ -x "$exe" ] || continue
    v=$("$exe" --version | awk '{print $2}')
    if [ "$(printf '%s\n5.036\n' "$v" | sort -V | head -n1)" = "5.036" ]; then
      echo "==> found Verilator $v at $exe"; return 0
    fi
  done
  return 1
}

if [ "${SKIP_VERILATOR:-0}" != 1 ] && ! have_ok_verilator; then
  echo "==> building Verilator $VLT_VER into $PREFIX"
  tmp=$(mktemp -d)
  git clone --depth 1 --branch "$VLT_VER" https://github.com/verilator/verilator "$tmp/verilator"
  (cd "$tmp/verilator" && autoconf && ./configure --prefix="$PREFIX" && make -j"$(nproc)" && sudo make install)
  rm -rf "$tmp"
fi

echo "==> Python venv (.venv) with cocotb + numpy"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo
echo "Done. Try:"
echo "  cd 01-uart && make test     # fails until you implement it; that's expected"
echo "  make lint"
