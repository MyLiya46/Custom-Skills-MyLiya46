#!/usr/bin/env python3
"""从 config.example.json 生成 config.json（首次初始化）。

定位规则：两个文件都位于本脚本所在目录的上一级（skill 安装目录根下），
由 _config_util.py 统一提供路径与读写/编码处理。

用法：
    python sync.py            # 若 config.json 不存在，从模板生成；已存在则跳过（不覆盖）
    python sync.py --force    # 强制用模板覆盖现有 config.json（会丢失本地改动，慎用）

退出码：
    0  成功（或已存在且无需处理）
    1  模板缺失 / JSON 非法
"""
import json
import sys

from _config_util import CONFIG_PATH, EXAMPLE_PATH, save_config, ensure_utf8_stdio


def main(argv: list[str]) -> int:
    ensure_utf8_stdio()
    force = "--force" in argv

    if CONFIG_PATH.exists() and not force:
        print("config.json 已存在，跳过（用 --force 覆盖）。")
        return 0

    if not EXAMPLE_PATH.exists():
        print("缺少 config.example.json，无法初始化。", file=sys.stderr)
        return 1

    with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 去掉模板里的注释键，得到干净的运行时配置
    config = {k: v for k, v in raw.items() if not str(k).startswith("//")}

    # 防御旧版/他人改过的模板仍带演示数据：项目条目与远程绝不进运行时配置（身份由 init_config.py 决定）
    config["project"] = {}
    config["remotes"] = []

    save_config(config)

    print(f"已生成 {CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))