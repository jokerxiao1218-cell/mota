#!/usr/bin/env bash
# 一键运行魔塔50层(自动使用项目自带虚拟环境,无需手动激活)
cd "$(dirname "$0")"
exec .venv/bin/python game/main.py "$@"
