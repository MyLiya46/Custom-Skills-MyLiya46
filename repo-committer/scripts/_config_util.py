#!/usr/bin/env python3
"""repo-committer 脚本共享工具：config.json 的定位、读写与 dotted 路径下钻。

三个脚本（read_config.py / write_config.py / project.py）以前各自复制一份
get/set/remove/load/save，边界行为已有细微漂移；统一放这里，避免「改了 A 没改 B」。

定位规则：config.json 固定在本文件所在目录的上一级（即 skill 安装目录根下），
不依赖运行时工作目录，也不硬编码任何机器绝对路径。
"""
import json
import os
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"
EXAMPLE_PATH = SKILL_ROOT / "config.example.json"


def ensure_utf8_stdio() -> None:
    """强制 stdout/stderr 以 UTF-8 输出，避免 Windows(GBK) 下中文被宿主按 UTF-8 读取时乱码。

    宿主（Claude Code/Codex 等）按 UTF-8 读取子进程输出；而 Windows 控制台/管道默认编码可能是
    GBK(cp936)，导致脚本中文被写成 GBK 字节、读方按 UTF-8 解析 → 乱码。`reconfigure`
    仅 Python 3.7+ 可用且只对文本流生效，故逐流 try/except 静默降级。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def load_config() -> dict:
    """读 config.json；不存在时回退 config.example.json（去注释键）；均无返回空 dict。"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    if EXAMPLE_PATH.exists():
        with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
            # 去掉模板里的注释键，得到干净的运行时配置
            return {k: v for k, v in raw.items() if not str(k).startswith("//")}
    return {}


def save_config(config: dict) -> None:
    # 先写临时文件再 os.replace 原子替换，避免并发读写读到半截文件
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)


def parse_value(raw: str):
    """值先按 JSON 字面量解析（true/false/数字/对象等），失败则当作字符串。"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def get_dotted(obj, dotted: str):
    """沿点号下钻取字段；数组用数字索引；中途缺失返回 None。"""
    cur = obj
    for key in dotted.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(key)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def set_dotted(obj: dict, dotted: str, value) -> None:
    """沿点号下钻写字段，中间缺失段自动补 dict/list（数字段补 list 并扩容）。"""
    parts = dotted.split(".")
    cur = obj
    for i, part in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        if isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                raise ValueError(f"数组需数字索引，收到：{part!r}（{dotted!r}）")
            while len(cur) <= idx:
                cur.append({})
            if not isinstance(cur[idx], (dict, list)):
                cur[idx] = {}
            cur = cur[idx]
        elif isinstance(cur, dict):
            existing = cur.get(part)
            if not isinstance(existing, (dict, list)):
                existing = [] if nxt.isdigit() else {}
                cur[part] = existing
            cur = existing
        else:
            raise ValueError(f"无法下钻：{part!r}（{dotted!r}）")

    if isinstance(cur, list):
        try:
            idx = int(parts[-1])
        except ValueError:
            raise ValueError(f"数组需数字索引，收到：{parts[-1]!r}（{dotted!r}）")
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    elif isinstance(cur, dict):
        cur[parts[-1]] = value
    else:
        raise ValueError(f"无法赋值：{dotted!r}")


def remove_dotted(obj, dotted: str) -> bool:
    """删除 dotted 指向的字段；数组用数字索引（删除该元素）。返回是否成功删除。"""
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return False
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False
    last = parts[-1]
    if isinstance(cur, list):
        try:
            idx = int(last)
            del cur[idx]
            return True
        except (ValueError, IndexError):
            return False
    elif isinstance(cur, dict) and last in cur:
        del cur[last]
        return True
    return False