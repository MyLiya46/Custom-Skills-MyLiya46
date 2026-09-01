#!/usr/bin/env python3
"""首次初始化：扫描本地 git 配置，生成 repo-committer 的 config.json。

背景：用户本机早已配置过 git（--global 等），只是当前仓库/首次使用本 skill 时还没有
config.json。本脚本扫描 `git config` 的既有身份，若当前目录是 git 仓库再扫描 remote/branch，
生成结构完整的 config.json（默认值 + 现场读取值 + 留空待定值），避免再次提问。

定位规则：config.json 固定在本脚本所在目录的上一级（skill 安装目录根下），不依赖
运行时工作目录，也不硬编码任何机器绝对路径。

扫描顺序（name / email 分别逐级，各自取第一个非空；与 git 自身 local > global > system
的生效优先级一致——最具体者优先，仓库级身份不会被全局/系统级覆盖）：
    1. git config --local   --get user.name / user.email（当前仓库，最具体）
    2. git config --global  --get user.name / user.email
    3. git config --system  --get user.name / user.email
均无 → 该字段留空（空串 = 未知，运行时按场景 C 问询），绝不填占位假值。

仓库现场值（仅当当前目录是 git 仓库时扫描，写入 project[<项目根>]）：
    git remote -v                    → 每个远程一条（name/url，branch 留空）
    git branch --show-current        → 当前分支

用法：
    python init_config.py           # config.json 已存在则跳过（不覆盖）
    python init_config.py --force   # 强制用扫描结果覆盖现有 config.json

退出码：
    0  成功
    1  git 不可用且无默认值 / 写入失败
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from _config_util import (
    SKILL_ROOT,
    CONFIG_PATH,
    EXAMPLE_PATH,
    ensure_utf8_stdio,
)

# git 输出按 UTF-8 解码（Windows 默认 locale 可能是 GBK，而 git 输出为 UTF-8）
GIT_ENCODING = {"encoding": "utf-8", "errors": "replace"}


def git_config(scope: str, key: str):
    """读取某个 scope 下的 git 配置项，读不到返回 None。"""
    args = ["git", "config", scope, "--get", key]
    try:
        result = subprocess.run(args, capture_output=True, text=True, **GIT_ENCODING)
    except OSError:
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def scan_git_identity() -> tuple[str, str]:
    """name / email 分别逐 --local → --global → --system 取第一个非空；均无则返回空串。

    顺序按 git 生效优先级 local > global > system（最具体者优先）：第一个非空值即锁定，
    故仓库级身份优先于全局/系统级，不会被覆盖。守卫是 first-win，切勿改成 last-win 覆盖语义。
    """
    name = email = ""
    for scope in ("--local", "--global", "--system"):
        if not name:
            name = git_config(scope, "user.name") or ""
        if not email:
            email = git_config(scope, "user.email") or ""
    return name, email


def find_project_root() -> Path:
    """向上找 .git（目录或文件），返回包含它的一级目录；找不到返回 cwd。"""
    cur = Path.cwd().resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    return cur


def is_git_repo(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-dir"],
        capture_output=True, text=True, **GIT_ENCODING,
    )
    return result.returncode == 0


def scan_remotes(root: Path) -> list[dict]:
    """扫描 `git remote -v`，每个远程一条（fetch/push 两条只取一条）。"""
    result = subprocess.run(
        ["git", "-C", str(root), "remote", "-v"],
        capture_output=True, text=True, **GIT_ENCODING,
    )
    out: list[dict] = []
    seen: set = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] in seen:
            continue
        seen.add(parts[0])
        out.append({
            "name": parts[0],
            "url": parts[1],
            "branch": "",
            "enabled": True,
        })
    return out


def load_template() -> dict:
    if EXAMPLE_PATH.exists():
        try:
            with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {k: v for k, v in raw.items() if not str(k).startswith("//")}
        except json.JSONDecodeError:
            pass
    return {}


def main(argv: list[str]) -> int:
    ensure_utf8_stdio()
    force = "--force" in argv

    if CONFIG_PATH.exists() and not force:
        print("config.json 已存在，跳过（用 --force 覆盖）。")
        return 0

    name, email = scan_git_identity()
    config = load_template()

    config["current_user"] = {"name": name, "email": email}

    # alternate_user：只保留扫描到的身份一条；身份为空则留空数组（由场景 C 问询补全）
    config["alternate_user"] = [{"name": name, "email": email}] if (name or email) else []

    # 全局默认行为（缺省即 true/false/zh）
    config.setdefault("behavior", {})
    config["behavior"].setdefault("ask_before_push", True)
    config["behavior"].setdefault("auto_include_untracked", False)
    config["behavior"].setdefault("auto_git_init", True)
    config.setdefault("commit_message_language", "zh")

    # project：先清掉模板可能残留的演示条目；当前目录是 git 仓库时写入现场 remote/branch
    config["project"] = {}
    root = find_project_root()
    if is_git_repo(root):
        remotes = scan_remotes(root)
        # branch 留空（空 = 「当前分支」语义）：不固化成非空快照，以免日后切分支后沿用它推错分支。
        # 仅当用户显式「固定推某分支」时才应填非空。
        config["project"][str(root)] = {
            "current_user": {"name": name, "email": email},
            "remotes": remotes,
        }
        print(f"已扫描当前仓库 remote 并写入 project[{root}]：{len(remotes)} 个 remote（branch 留空 = 跟随当前分支）")

    # 先写临时文件再 os.replace 原子替换，避免并发读写读到半截文件（D2）
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)

    print(f"已扫描本地 git 配置并生成 {CONFIG_PATH}")
    print(f"  current_user = {name} <{email}>")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))