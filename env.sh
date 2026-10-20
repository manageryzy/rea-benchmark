# Source this file from Bash or Zsh to use the workspace tools.
export REA_ENV_ROOT=/mnt/h/work/rea
export PATH="$REA_ENV_ROOT/bin:$REA_ENV_ROOT/.tools/node-v24.22.0-linux-x64/bin:$PATH"
if [ -x "$REA_ENV_ROOT/.tools/go1.23.2/bin/go" ]; then
  export PATH="$REA_ENV_ROOT/.tools/go1.23.2/bin:$PATH"
fi
export GHIDRA_INSTALL_DIR="${GHIDRA_INSTALL_DIR:-$REA_ENV_ROOT/.tools/ghidra_12.1.4_PUBLIC}"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk-amd64}"
export REA_ANALYSIS_PROVIDER="${REA_ANALYSIS_PROVIDER:-ghidra}"
export GHIDRA_HEADLESS_MAXMEM="${GHIDRA_HEADLESS_MAXMEM:-2G}"
export REA_MCP_INPUT_SCHEMA_PROFILE="${REA_MCP_INPUT_SCHEMA_PROFILE:-compact}"
