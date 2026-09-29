#!/usr/bin/env bash
# 跑后端全部测试。用法：bash run_tests.sh [测试路径]
# 例：bash run_tests.sh party.tests_survivor
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
exec .venv/Scripts/python.exe manage.py test "${1:-party}" -v "${VERBOSE:-1}"
