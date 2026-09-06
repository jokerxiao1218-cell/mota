#!/usr/bin/env bash
# 一键运行全部单元测试(自动使用项目自带虚拟环境)
cd "$(dirname "$0")"
exec .venv/bin/python -m pytest tests/ -v "$@"
