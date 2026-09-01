#!/usr/bin/env python3
"""repo-committer 的项目级命令：解析当前项目、登记/读写 project 字段、隐私拦截（gitignore）。

思想：类似 Claude 记录会话——skill config 里有一个 `project` 字段，按「项目根目录绝对路径」
为 key，记录每个项目的属地配置（remote / branch / email 等，不含 gitignore，也不含 SSH key）。

定位规则：config.json 固定在本脚本所在目录的上一级（skill 安装目录根下），不依赖运行时
工作目录，也不硬编码任何机器绝对路径。

术语：
    git config  —— 本机 / 本仓库的 git 原生配置（user/remote/branch/.gitignore 等），由 git 管。
    skill config—— repo-committer 的 config.json，记录扫描来的身份与每个项目的落地设置。

子命令：
    python project.py resolve-project             # 输出「当前项目根」绝对路径（向上找 .git，找不到取 cwd）
    python project.py register                    # 若 project 尚无当前项目 key，则登记一个空条目（不覆盖）
    python project.py get                         # 输出当前项目的配置条目（无则输出空对象 + 退出码 1）
    python project.py get <dotted>                # 输出当前项目条目的某个字段（支持数组索引）
    python project.py set <dotted> <value>        # 写入当前项目条目的某个字段（自动 register，幂等）
    python project.py remove <dotted>             # 删除当前项目条目的某个字段（支持数组索引）
    python project.py gitignore                   # 隐私拦截：扫描「即将 git add 的文件」，补写/提示敏感忽略项
    python project.py gitignore --all             # 非 git 仓库时强制全目录扫描（默认按 git status 取待提交文件）
    python project.py gitignore --list            # 打印内置共识忽略清单（JSON 数组）
    python project.py gitignore --dry-run         # 只扫描报告，不写 .gitignore（预览拟补写项）

退出码：
    0  成功（隐私拦截只是检查/提示，不阻断）
    1  项目未登记 / JSON 非法
    2  参数错误
"""
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

from _config_util import (
    SKILL_ROOT,
    load_config,
    save_config,
    parse_value,
    get_dotted,
    set_dotted,
    remove_dotted,
    ensure_utf8_stdio,
)

# git 输出按 UTF-8 解码（Windows 默认 locale 可能是 GBK，而 git 文件名/输出为 UTF-8）
GIT_ENCODING = {"encoding": "utf-8", "errors": "replace"}

# 共识忽略清单：公共认知、可安全自动补写进项目根 .gitignore 的「模式」（如 .env、node_modules/）
DEFAULT_GITIGNORE = [
    ".env",
    ".env.*",
    "*.local",
    "node_modules/",
]

# `.env.*` 的放行白名单：这些是普遍**应当提交**的模板文件，补写 `.env.*` 时一并追加
# `!` 反选放行（需位于 `.env.*` 之后才能生效），避免误吞 `.env.example` 等。
GITIGNORE_REINCLUDE = [
    "!.env.example",
    "!.env.sample",
    "!.env.template",
    "!.env.dist",
]

# 全目录扫描边界：目录深度与候选文件数上限，超限提示而非静默截断
MAX_SCAN_DEPTH = 32
MAX_SCAN_FILES = 5000

# 经验敏感清单：疑似隐私/凭证文件（除共识项外）；扫到即提示用户确认，不擅自加入
SENSITIVE_PATTERNS = [
    "*.pem", "*.key", "*.p12", "*.pfx", "*.crt", "*.cer", "*.jks", "*.keystore",
    "id_rsa", "id_rsa.*", "id_ed25519", "id_ed25519.*", "id_ecdsa", "id_ecdsa.*",
    "credentials", "credentials.json", "credentials.yml", "secrets", "secrets.*",
    ".npmrc", ".pypirc", ".htpasswd",
    ".aws/credentials", ".kube/config", ".docker/config.json",
]

# 内容标记：完整 PEM 私钥头（用拼接构造，避免源码里出现会被自匹配的连续字面量）。
# 只匹配完整头而非宽泛的 "PRIVATE KEY"，杜绝文档/源码里提到该词时自我误报。
_PEM_BEGIN = b"-----BEGIN "
SECRET_CONTENT_MARKERS = [
    _PEM_BEGIN + b"OPENSSH PRIVATE KEY-----",
    _PEM_BEGIN + b"RSA PRIVATE KEY-----",
    _PEM_BEGIN + b"EC PRIVATE KEY-----",
    _PEM_BEGIN + b"ENCRYPTED PRIVATE KEY-----",
]


def find_project_root() -> Path:
    """向上找 .git（目录或文件），返回包含它的一级目录；找不到返回 cwd。"""
    cur = Path.cwd().resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    return cur


def cmd_resolve() -> int:
    print(find_project_root())
    return 0


def cmd_register(config: dict) -> int:
    root = str(find_project_root())
    if "project" not in config or not isinstance(config["project"], dict):
        config["project"] = {}
    if root not in config["project"]:
        config["project"][root] = {}
        save_config(config)
        print(f"已登记项目：{root}")
    else:
        print(f"项目已登记：{root}")
    return 0


def cmd_get(config: dict, argv: list[str]) -> int:
    root = str(find_project_root())
    project = config.get("project") or {}
    entry = project.get(root)
    if entry is None:
        print("{}", file=sys.stderr)
        return 1

    if len(argv) >= 3:
        value = get_dotted(entry, argv[2])
        if value is None:
            print(f"字段缺失：{argv[2]}", file=sys.stderr)
            return 1
        if isinstance(value, (dict, list)):
            print(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            print(value)
    else:
        print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def cmd_set(config: dict, argv: list[str]) -> int:
    if len(argv) < 4:
        print("缺少参数：project.py set <dotted> <value>", file=sys.stderr)
        return 2
    root = str(find_project_root())
    if "project" not in config or not isinstance(config["project"], dict):
        config["project"] = {}
    entry = config["project"].setdefault(root, {})
    set_dotted(entry, argv[2], parse_value(argv[3]))
    save_config(config)
    print(json.dumps(config["project"][root], ensure_ascii=False, indent=2))
    return 0


def cmd_remove(config: dict, argv: list[str]) -> int:
    if len(argv) < 3:
        print("缺少参数：project.py remove <dotted>", file=sys.stderr)
        return 2
    root = str(find_project_root())
    entry = (config.get("project") or {}).get(root)
    if entry is None:
        print(f"项目未登记：{root}", file=sys.stderr)
        return 1
    if not remove_dotted(entry, argv[2]):
        print(f"字段不存在：{argv[2]}", file=sys.stderr)
        return 1
    save_config(config)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# 隐私拦截（gitignore）
# ---------------------------------------------------------------------------

def is_git_repo(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-dir"],
        capture_output=True, text=True, **GIT_ENCODING,
    )
    return result.returncode == 0


def candidate_files(root: Path, scan_all: bool):
    """返回 (候选文件相对 root 的路径列表, 是否来自 git status)。

    - 有 git 仓库且非 --all：用 `git status --porcelain` 取「即将 git add 的候选」
      （排除删除态；重命名取新路径）。
    - 非 git 仓库或 --all：全目录扫描（跳过 .git 与 __pycache__）。
    """
    if not scan_all and is_git_repo(root):
        # 用 -z 按 NUL 分片，避免 core.quotePath 对中文/空格/特殊字符路径的转义被当字面量；
        # 重命名记录为 "R  <新路径>\0<旧路径>\0"——新路径在前，紧跟的旧路径仅一条（状态位以空格补齐）。
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "-z", "-uall"],
            capture_output=True, text=True, **GIT_ENCODING,
        )
        paths = []
        toks = iter(result.stdout.split("\0"))
        for tok in toks:
            if not tok:
                continue
            x, y = tok[0], tok[1] if len(tok) > 1 else " "
            if x == "R":  # 重命名：下一 token 是旧路径，跳过
                next(toks, "")
                tok = tok[3:]
            else:
                if x == "D" or y == "D":  # 删除态（已暂存或未暂存）：文件磁盘上已不存在，不纳入候选
                    continue
                tok = tok[3:]  # 普通条目："XY <路径>"，去掉 3 字符状态前缀
            if tok:
                paths.append(tok)
        return paths, True

    # 全目录扫描：跳过 .git、__pycache__、以及 skill 自身目录（避免把本 skill 的 config 等当候选）。
    # 加深度/数量上限，避免在 ~ 或大目录运行时过慢、刷屏；超限打印提示而非静默截断。
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if len(rel.parts) > MAX_SCAN_DEPTH:
            continue
        if ".git" in rel.parts or "__pycache__" in rel.parts:
            continue
        if str(rel) == ".gitignore":
            continue
        try:
            p.resolve().relative_to(SKILL_ROOT)
            continue  # 位于 skill 自身目录内，排除
        except ValueError:
            pass
        paths.append(str(rel).replace("\\", "/"))
        if len(paths) >= MAX_SCAN_FILES:
            print("⚠️ 扫描范围过大：候选文件已达上限，仅扫描前"
                  f" {MAX_SCAN_FILES} 个。建议在具体项目目录内运行。", file=sys.stderr)
            break
    return paths, False


def ignored_paths(root: Path, paths: list[str]) -> set:
    """返回已被 git 忽略的路径集合（含嵌套 .gitignore / .git/info/exclude / 全局 excludesFile）。

    用 `git check-ignore --stdin` 让 git 自己解析所有层级的忽略规则——天然正确处理嵌套 .gitignore。
    """
    if not paths or not is_git_repo(root):
        return set()
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--stdin"],
        input="\n".join(paths), capture_output=True, text=True, **GIT_ENCODING,
    )
    return set(result.stdout.splitlines())


def consensus_pattern(path: str):
    """path 命中共识清单则返回命中的「模式」，否则 None。"""
    p = path.replace("\\", "/")
    name = p.split("/")[-1]
    for pat in DEFAULT_GITIGNORE:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(p, pat):
            return pat
        if pat.endswith("/") and p.startswith(pat):
            return pat
    return None


def is_sensitive_name(path: str) -> bool:
    p = path.replace("\\", "/")
    name = p.split("/")[-1]
    for pat in SENSITIVE_PATTERNS:
        if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(p, pat):
            return True
    return False


def has_secret_content(root: Path, rel: str) -> bool:
    f = root / rel
    try:
        with open(f, "rb") as fh:
            head = fh.read(8192)
    except OSError:
        return False
    return any(m in head for m in SECRET_CONTENT_MARKERS)


def read_gitignore_lines(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    with open(gi, "r", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f.readlines()]


def append_gitignore_lines(root: Path, lines: list[str]) -> None:
    """把缺失的「模式/路径」追加到项目根 .gitignore（保序追加，去重）。"""
    existing = read_gitignore_lines(root)
    missing = [ln for ln in lines if ln not in existing]
    if not missing:
        return
    merged = existing + ([""] if existing and existing[-1] != "" else []) + missing
    gi = root / ".gitignore"
    with open(gi, "w", encoding="utf-8") as f:
        f.write("\n".join(merged).rstrip("\n") + "\n")


def cmd_gitignore(argv: list[str]) -> int:
    """隐私拦截：只针对「即将 git add 的新改动」判断敏感项。

    原则：
    - 不看已提交过的东西，只扫待提交候选（git status；非 git 仓库则全目录）。
    - 已被 .gitignore 覆盖的（含嵌套，用 git check-ignore 判断）视为用户有意为之，跳过。
    - 命中共识清单（.env / .env.* / *.local / node_modules/）→ 自动补写模式；
      补写 `.env.*` 时同时追加 `!.env.example` 等放行白名单，避免误吞应提交的模板文件。
    - 内容含私钥标记 → 自动补写该文件路径。
    - 命中经验敏感清单（密钥/证书/凭证等）→ 打印出来，交由 skill 层询问用户确认。
    """
    if "--list" in argv:
        print(json.dumps(DEFAULT_GITIGNORE, ensure_ascii=False))
        return 0

    dry_run = "--dry-run" in argv
    root = find_project_root()
    scan_all = "--all" in argv
    paths, from_git = candidate_files(root, scan_all)

    ignored = ignored_paths(root, paths)
    pending = [p for p in paths if p not in ignored]

    auto_add: list[str] = []
    suspicious: list[str] = []

    for rel in pending:
        pat = consensus_pattern(rel)
        if pat:
            if pat not in auto_add:
                auto_add.append(pat)
        elif has_secret_content(root, rel):
            if rel not in auto_add:
                auto_add.append(rel)
        elif is_sensitive_name(rel):
            suspicious.append(rel)

    # `.env.*` 会误吞 `.env.example` 等模板文件：补写 `.env.*` 时，追加放行白名单
    # 才能让这些模板保持可提交（git 的 ! 反选必须位于忽略规则之后，故放在 auto_add 末尾）。
    if ".env.*" in auto_add:
        for ln in GITIGNORE_REINCLUDE:
            if ln not in auto_add:
                auto_add.append(ln)

    source_desc = "git status 待提交候选" if from_git else "全目录扫描（非 git 仓库）"

    print(f"隐私拦截扫描（{source_desc}）：")
    print(f"  待提交候选：{len(paths)} 个")
    print(f"  已被 .gitignore 覆盖（含嵌套）：{len(ignored)} 个，跳过")
    print(f"  未忽略候选：{len(pending)} 个")

    if auto_add:
        if dry_run:
            print("[预览] 拟补写 .gitignore 项（未写入）：")
            for ln in auto_add:
                print(f"  + {ln}")
        else:
            append_gitignore_lines(root, auto_add)
            print(f"自动补写 {root / '.gitignore'} 项（共识/明确私钥）：")
            for ln in auto_add:
                print(f"  + {ln}")
    else:
        print("  无需自动补写。")

    if suspicious:
        print("发现疑似敏感文件（未忽略，建议确认后加入 .gitignore）：")
        for rel in suspicious:
            print(f"  - {rel}")
    else:
        print("  未发现其它疑似敏感文件。")

    return 0


def main(argv: list[str]) -> int:
    ensure_utf8_stdio()
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    cmd = argv[1]
    config = load_config()

    if cmd == "resolve-project":
        return cmd_resolve()
    if cmd == "register":
        return cmd_register(config)
    if cmd == "get":
        return cmd_get(config, argv)
    if cmd == "set":
        return cmd_set(config, argv)
    if cmd == "remove":
        return cmd_remove(config, argv)
    if cmd == "gitignore":
        return cmd_gitignore(argv)

    print(f"未知子命令：{cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))