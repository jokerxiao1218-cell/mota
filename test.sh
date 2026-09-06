#!/usr/bin/env bash
# 一键运行全部单元测试(自动使用项目自带虚拟环境)
cd "$(dirname "$0")"
unset PYTHONPATH   # 屏蔽系统环境变量(如 ROS)对 venv 的污染
exec .venv/bin/python -m pytest tests/ -v "$@"
