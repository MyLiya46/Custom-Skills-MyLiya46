#!/usr/bin/env python3
"""读取 repo-committer 的 config.json。

定位规则与 dotted 下钻见 _config_util.py（与 write_config.py / project.py 共用一个实现）。

用法：
    python read_config.py                 # 输出完整 JSON
    python read_config.py user.name       # 输出单个字段值
    python read_config.py remotes.0.url   # 数组字段：数字 key 按列表索引下钻
    python read_config.py --check         # 静默校验必填字段，缺失则非 0 退出

退出码：
    0  读取成功（且 --check 时字段齐备）
    1  文件不存在 / JSON 非法 / 字段缺失

--check 校验提交身份：优先 current_user，回退 user（兼容旧版配置）。
"""
import json
import sys

from _config_util import load_config, get_dotted, ensure_utf8_stdio


def main(argv: list[str]) -> int:
    ensure_utf8_stdio()
    if "--check" in argv:
        try:
            config = load_config()
        except json.JSONDecodeError:
            config = {}
        identity = config.get("current_user") or config.get("user") or {}
        if not identity.get("name") or not identity.get("email"):
            print("缺省：config.json 缺少 current_user.name / current_user.email", file=sys.stderr)
            return 1
        return 0

    try:
        config = load_config()
    except json.JSONDecodeError as e:
        print(f"config.json 解析失败：{e}", file=sys.stderr)
        sys.exit(1)

    if len(argv) >= 2 and not argv[1].startswith("-"):
        value = get_dotted(config, argv[1])
        if value is None:
            print(f"字段缺失：{argv[1]}", file=sys.stderr)
            return 1
        print(value)
        return 0

    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))