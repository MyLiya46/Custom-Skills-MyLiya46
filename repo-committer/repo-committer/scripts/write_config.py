#!/usr/bin/env python3
"""写入（更新）repo-committer 的 config.json，幂等。

定位规则与 dotted 下钻见 _config_util.py（与 read_config.py / project.py 共用一个实现）。

用法：
    python write_config.py current_user.email you-alt@example.com
    python write_config.py user.name "Jie Guo"
    python write_config.py behavior.ask_before_push false
    python write_config.py remotes.0.enabled false     # 数组字段：数字 key 按列表索引下钻
    python write_config.py remove remotes.1            # 删除 remotes 数组第 1 项（或 remove 某个 dotted 字段）

    # 通过 JSON 一次性赋值（用于新增/删除 remote、批量修改等结构性变更）
    python write_config.py '{"remotes": [{"name": "github", "url": "...", "branch": "main", "enabled": true}]}'

说明：
  - dotted 键名按点号逐级下钻；中间段是数字则走列表索引（不存在则扩容）。
  - 值会尝试按 JSON 字面量解析（true/false/数字/对象等），失败则当作字符串。
  - 文件不存在时，优先从 config.example.json 复制一份再改；两者都没有则从空对象开始。
"""
import json
import sys

from _config_util import (
    load_config,
    save_config,
    parse_value,
    set_dotted,
    remove_dotted,
    ensure_utf8_stdio,
)


def main(argv: list[str]) -> int:
    ensure_utf8_stdio()
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    first = argv[1]
    config = load_config()

    if first == "remove":
        if len(argv) < 3:
            print("缺少参数：write_config.py remove <dotted>", file=sys.stderr)
            return 2
        if not remove_dotted(config, argv[2]):
            print(f"字段不存在：{argv[2]}", file=sys.stderr)
            return 1
    elif first.strip().startswith("{"):
        # JSON 批量赋值模式
        patch = json.loads(first)
        for key, value in patch.items():
            config[key] = value
    else:
        if len(argv) < 3:
            print("缺少参数：write_config.py <dotted> <value>", file=sys.stderr)
            return 2
        set_dotted(config, first, parse_value(argv[2]))

    save_config(config)

    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))