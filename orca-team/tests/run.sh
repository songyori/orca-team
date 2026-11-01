#!/usr/bin/env bash
# orca-team 테스트 전체 실행. 가짜 orca·codex·Codex 래퍼를 쓰므로 실제 Orca 상태를 건드리지 않는다.
# 내장 Codex 래퍼 회귀 테스트(test-codex-run.sh)는 2분 넘게 걸려 기본 실행에서 뺀다.
# `--wrapper` 인자나 ORCA_TEAM_TEST_WRAPPER=1 이면 Python 테스트 뒤에 그것도 돌린다.
set -euo pipefail
cd "$(dirname "$0")"
wrapper="${ORCA_TEAM_TEST_WRAPPER:-0}"
for arg in "$@"; do
  case "$arg" in
    --wrapper) wrapper=1 ;;
    *) echo "알 수 없는 인자: $arg (사용법: run.sh [--wrapper])" >&2; exit 2 ;;
  esac
done
python3 -m unittest discover -s . -p 'test_*.py' -v
if [ "$wrapper" = 1 ]; then
  bash ./test-codex-run.sh
fi
