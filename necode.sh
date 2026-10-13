#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: necode.sh [--paste] FILE

  --paste       Read clipboard input; retry runtime failures without debug flags.
  -h, --help    Show this help.

Environment:
  CXX              Compiler executable (auto-detected if unset).
  NECODE_ROOT      Project root (default: source Git root or source directory).
  NECODE_OUT_DIR   Output directory (default: project root/out).
  NECODE_ACL_DIR   AtCoder Library directory (default: project root/ac-library).
EOF
}
fail() { printf 'necode: %s\n' "$*" >&2; exit 1; }

paste=false
source_file=''
while (($#)); do
  case "$1" in
    --paste) paste=true ;;
    -h|--help) usage; exit 0 ;;
    --) shift; (($# == 1)) || fail 'Expected one source file after --.'; source_file=$1; break ;;
    -*) fail "Unknown option: $1" ;;
    *) [[ -z "$source_file" ]] || fail 'Expected exactly one source file.'; source_file=$1 ;;
  esac
  shift
done
[[ -n "$source_file" ]] || { usage >&2; exit 1; }
[[ -f "$source_file" ]] || fail "Source file not found: $source_file"
source_dir=$(cd -- "$(dirname -- "$source_file")" && pwd -P)
source_file="$source_dir/$(basename -- "$source_file")"

if [[ -n "${NECODE_ROOT:-}" ]]; then
  root=$(cd -- "$NECODE_ROOT" && pwd -P)
elif root=$(git -C "$source_dir" rev-parse --show-toplevel 2>/dev/null); then
  root=$(cd -- "$root" && pwd -P)
else
  root=$source_dir
fi
case "$source_file" in
  "$root"/*) relative=${source_file#"$root"/} ;;
  *) fail 'Source file must be inside NECODE_ROOT.' ;;
esac
relative=${relative%.*}
[[ "$relative" == */* ]] || relative="main/$relative"
output="${NECODE_OUT_DIR:-$root/out}/$relative"
[[ "$output" != "$source_file" ]] || fail 'Output would overwrite the source file.'

compiler=${CXX:-}
if [[ -z "$compiler" ]]; then
  for candidate in g++-16 g++-15 g++-14 g++ clang++; do
    if command -v "$candidate" >/dev/null 2>&1; then
      compiler=$candidate
      break
    fi
  done
fi
[[ -n "$compiler" ]] || fail 'No C++ compiler found. Install one or set CXX.'
command -v "$compiler" >/dev/null 2>&1 || fail "Compiler not found: $compiler"
if "$paste"; then
  command -v pbpaste >/dev/null 2>&1 || fail '--paste requires pbpaste (macOS).'
fi

flags=(-std=c++23 -O2)
debug_flags=(-Wall -Wextra -Wshadow -D_GLIBCXX_DEBUG)
acl=${NECODE_ACL_DIR:-$root/ac-library}
if [[ -d "$acl" ]]; then
  flags+=("-I" "$acl")
elif [[ -n "${NECODE_ACL_DIR:-}" ]]; then
  fail "AtCoder Library directory not found: $acl"
fi
mkdir -p -- "$(dirname -- "$output")"
if ! "$compiler" "${flags[@]}" "${debug_flags[@]}" "$source_file" -o "$output"; then
  fail 'Compile Error'
fi

# Route only runtime stderr through a line formatter. Stdout remains untouched.
run_dir=$(mktemp -d "${TMPDIR:-/tmp}/necode.XXXXXXXX")
trap 'rm -rf -- "$run_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
stderr_pipe="$run_dir/stderr"
mkfifo "$stderr_pipe"
terminal=0
color=0
if [[ -t 2 ]]; then
  terminal=1
  if [[ "${TERM:-}" != dumb && -z "${NO_COLOR:-}" ]]; then
    color=1
  fi
fi
run_program() {
  local result=0 reader
  awk -v terminal="$terminal" -v color="$color" '
    NR == 1 && terminal { printf "\n" }
    {
      if (color) printf "\033[33m[stderr]\033[0m %s\n", $0
      else printf "[stderr] %s\n", $0
      fflush()
    }
  ' < "$stderr_pipe" >&2 &
  reader=$!
  "$@" 2> "$stderr_pipe" || result=$?
  # Drain the formatter before printing a retry header or exiting.
  wait "$reader" || :
  return "$result"
}

# Manual mode inherits stdin directly: no input buffering or extra EOF required.
if ! "$paste"; then
  status=0
  run_program "$output" || status=$?
  exit "$status"
fi

# Only clipboard mode can replay the full input automatically.
input="$run_dir/input"
pbpaste | sed $'s/\033\\[200~//g; s/\033\\[201~//g' > "$input"
if [[ -t 1 ]]; then
  cat -- "$input"
fi

status=0
run_program "$output" < "$input" || status=$?
if ((status == 0)); then
  exit 0
fi
# User interruption is not a reason to run the program again.
if ((status == 130 || status == 143)); then
  exit "$status"
fi
printf '\nnecode: 検査ありの実行が終了コード %s で失敗しました。\n' "$status" >&2
printf 'necode: 警告・デバッグ指定を外し、同じ入力で再コンパイル・再実行します。\n' >&2
printf 'necode: 不正アクセスがある場合、再実行の結果も正しいとは限りません。\n' >&2
fallback="$run_dir/without-debug"
if ! "$compiler" "${flags[@]}" "$source_file" -o "$fallback"; then
  printf 'necode: 再コンパイルに失敗しました。\n' >&2
  exit "$status"
fi
printf '\n--- 検査なしの実行結果 ---\n' >&2
fallback_status=0
run_program "$fallback" < "$input" || fallback_status=$?
printf '\n--- 検査なしの終了コード: %s ---\n' "$fallback_status" >&2
# The original failure stays visible to callers even if the fallback succeeds.
exit "$status"
