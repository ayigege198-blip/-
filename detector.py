"""检测引擎 - 检测软件是否安装并提取版本号"""
import subprocess
import re
import os
import shutil
from pathlib import Path


def get_windowsapps_dir() -> Path:
    """当前用户的 WindowsApps 目录；winget 的命令别名通常在这里。"""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "Microsoft" / "WindowsApps"
    return Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps"


_WINGET_CACHE = None  # None=未计算, ""=确认不可用, str=可用的 winget 路径

WINGET_MIN_VERSION = "1.12.350"  # 低于此版本视为过低，预检查会自动更新


def _version_tuple(text: str):
    """从文本里抽出版本号转成可比较的元组；抽不到返回 None。"""
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?", text or "")
    if not m:
        return None
    return tuple(int(g) if g else 0 for g in m.groups())


def winget_meets_minimum(version: str, minimum: str = WINGET_MIN_VERSION) -> bool:
    """winget 版本号是否 >= 最低要求。"""
    current = _version_tuple(version)
    if current is None:
        return False
    return current >= _version_tuple(minimum)


def reset_winget_cache() -> None:
    """清掉 winget 探测缓存，安装/升级后需要重新检测时调用。"""
    global _WINGET_CACHE
    _WINGET_CACHE = None


def _winget_runs(path: str, timeout: int = 20) -> bool:
    """确认这个 winget 路径真的能执行。

    精简版/无商店 Windows 上，WindowsApps 里常残留一个 winget.exe 执行别名存根：
    文件存在(shutil.which/exists 都为真)，但实际运行会报“不是内部或外部命令”或弹商店。
    只有 `--version` 真正返回 0 才算可用，否则视为没有 winget。

    禁用更新/精简系统上首次冷启动很慢，超时设 20s 并重试一次，避免把“慢”误判成“没有”。
    """
    for _ in range(2):
        try:
            result = subprocess.run(
                f'"{path}" --version',
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="ignore",
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            if result.returncode == 0 and (result.stdout or "").strip():
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    return False


def find_winget() -> str:
    """找到一个真正能用的 winget.exe；找不到返回 ""。

    只缓存“已确认可用的路径”和“确认磁盘上根本没有 winget.exe”两种状态。
    若 exe 存在但本次 --version 没跑通(精简版/禁用更新时冷启动很慢甚至超时)，
    不写永久缓存，下次检测会重试，避免把一次“慢”永久误判成“没装”。
    """
    global _WINGET_CACHE
    if _WINGET_CACHE:  # 只信任已确认可用的缓存路径
        return _WINGET_CACHE

    # 1) C:\Program Files\WindowsApps 里的真实包二进制：存在即代表 winget 已安装。
    #    这是最权威的信号，不依赖能否运行(禁用更新时运行慢/弹商店但 winget 仍在)。
    #    去掉架构限定(_x64/_arm64/_x86 都匹配)。
    windows_apps = Path(r"C:\Program Files\WindowsApps")
    if windows_apps.exists():
        try:
            package_bins = sorted(
                windows_apps.glob("Microsoft.DesktopAppInstaller_*__8wekyb3d8bbwe/winget.exe"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for binary in package_bins:
                _WINGET_CACHE = str(binary)
                return _WINGET_CACHE
        except OSError:
            pass  # WindowsApps 可能因 ACL 不可读，退回别名探测

    # 2) PATH / 用户 WindowsApps 里的别名存根：必须真正能运行才算数(存根可能是死链)。
    alias_candidates: list[str] = []
    found = shutil.which("winget")
    if found:
        alias_candidates.append(found)
    alias_candidates.append(str(get_windowsapps_dir() / "winget.exe"))

    any_exe_present = False
    seen: set[str] = set()
    for candidate in alias_candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if not Path(candidate).exists():
            continue
        any_exe_present = True
        if _winget_runs(candidate):
            _WINGET_CACHE = candidate
            return candidate

    if any_exe_present:
        # exe 在但本次没跑通：可能只是冷启动超时，不缓存，下次重试。
        return ""
    _WINGET_CACHE = ""  # 磁盘上确实没有 winget.exe，缓存为不可用
    return ""


def _run(cmd: str, timeout: int = 8) -> tuple[int, str]:
    """执行命令,返回 (returncode, stdout+stderr)。失败返回 (-1, '')。"""
    try:
        if cmd.strip().lower().startswith("winget "):
            winget = find_winget()
            if not winget:
                return -1, (
                    "未检测到 winget。若已安装却仍报此提示，多半是禁用了 Windows 更新"
                    "导致应用商店/winget 无法启动：请先恢复 Windows Update、BITS 等服务再重试，"
                    "或安装/修复 Microsoft App Installer。"
                )
            cmd = f'"{winget}" {cmd.strip()[len("winget "):]}'
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
    winget = find_winget()
    if not winget:
        return False, (
            "未检测到 winget。若电脑禁用了 Windows 更新，会导致应用商店/winget 无法启动，"
            "请先恢复 Windows Update 相关服务再重试；否则请安装/修复 Microsoft App Installer。"
        )
    code, out = _run("winget --version")
    if code == 0 and out.strip():
        return True, _extract_version(out)
    return False, out.strip() or "winget 无法正常运行"


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
