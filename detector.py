"""检测引擎 - 检测软件是否安装并提取版本号"""
import subprocess
import re
import os
from pathlib import Path


def _run(cmd: str, timeout: int = 8) -> tuple[int, str]:
    """执行命令,返回 (returncode, stdout+stderr)。失败返回 (-1, '')。"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="ignore",
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return -1, ""


def _extract_version(text: str) -> str:
    """从文本中提取版本号,例如 'git version 2.52.0' -> '2.52.0'"""
    if not text:
        return ""
    # 匹配 v1.2.3 / 1.2.3 / 1.2.3.4 等格式
    m = re.search(r"v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)", text)
    return m.group(1) if m else text.strip().splitlines()[0][:40]


def detect_by_command(cmd: str) -> tuple[bool, str]:
    """通过执行命令检测,返回 (是否安装, 版本号)"""
    code, out = _run(cmd)
    if code == 0 and out.strip():
        return True, _extract_version(out)
    return False, ""


def detect_by_winget(winget_id: str) -> tuple[bool, str]:
    """通过 winget list 检测。winget 输出列宽不固定,按 ID 在行中的位置切片取版本号。"""
    code, out = _run(f'winget list --id "{winget_id}" --exact', timeout=15)
    if code != 0 or not out:
        return False, ""
    for line in out.splitlines():
        idx = line.lower().find(winget_id.lower())
        if idx < 0:
            continue
        # ID 后面的剩余内容:版本 [可用版本] 来源
        rest = line[idx + len(winget_id):].strip()
        if not rest:
            return True, ""
        # 第一个空白分割出的就是当前版本
        first_token = rest.split()[0]
        return True, _extract_version(first_token) or first_token
    return False, ""


def detect_by_windowsapps(pattern: str, version_regex: str = "") -> tuple[bool, str]:
    """通过 WindowsApps 目录检测 MSIX/桌面应用安装状态。"""
    root = Path(r"C:\Program Files\WindowsApps")
    if not root.exists():
        return False, ""
    try:
        matches = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return False, ""
    if not matches:
        return False, ""
    name = matches[0].name
    if version_regex:
        m = re.search(version_regex, name)
        if m:
            return True, m.group(1)
    return True, ""


def detect_by_paths(paths: list[str]) -> tuple[bool, str]:
    """通过一组常见路径检测安装状态。支持 %LOCALAPPDATA% 这类环境变量。"""
    for raw_path in paths:
        expanded = os.path.expandvars(raw_path)
        path = Path(expanded)
        if not path.exists():
            continue
        if path.suffix.lower() == ".exe":
            try:
                import win32api  # type: ignore
                info = win32api.GetFileVersionInfo(str(path), "\\")
                ms = info["FileVersionMS"]
                ls = info["FileVersionLS"]
                version = f"{ms >> 16}.{ms & 0xffff}.{ls >> 16}.{ls & 0xffff}"
                return True, version
            except Exception:
                return True, ""
        return True, ""
    return False, ""


def detect(item: dict) -> tuple[bool, str]:
    """按 software_config 中的 detect 配置检测"""
    rule = item.get("detect", {})
    rtype = rule.get("type")
    if rtype == "command":
        return detect_by_command(rule["cmd"])
    if rtype == "winget":
        return detect_by_winget(rule["id"])
    if rtype == "windowsapps":
        return detect_by_windowsapps(rule["pattern"], rule.get("version_regex", ""))
    if rtype == "paths":
        return detect_by_paths(rule.get("paths", []))
    return False, ""


def check_winget_available() -> tuple[bool, str]:
    """检查 winget 本身是否可用"""
    return detect_by_command("winget --version")


if __name__ == "__main__":
    # 命令行自测
    from software_config import CORE_STACK, EXTERNAL_TOOLS

    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    print("=== 检测 winget ===")
    ok, ver = check_winget_available()
    print(f"winget: {'[OK]' if ok else '[X]'}  {ver}")
    print()

    print("=== 核心环境 ===")
    for item in CORE_STACK:
        ok, ver = detect(item)
        status = f"已安装 v{ver}" if ok else "未安装"
        print(f"  {item['name']:25} {status}")

    print()
    print("=== 可选应用 ===")
    for item in EXTERNAL_TOOLS:
        ok, ver = detect(item)
        status = f"已安装 v{ver}" if ok else "未安装"
        print(f"  {item['name']:25} {status}")
