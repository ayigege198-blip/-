"""安装引擎 - 封装 winget/npm 安装,流式输出日志"""
import os
import re
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterator

from detector import find_winget


WINGET_NO_APPLICABLE_UPDATE = 0x8A15002B
WINGET_NO_APPLICABLE_UPDATE_SIGNED = WINGET_NO_APPLICABLE_UPDATE - 2**32

NO_UPDATE_MARKERS = (
    "找不到可用的升级",
    "没有可用的较新的包版本",
    "no available upgrade",
    "no applicable update",
    "no newer package versions",
    "no newer version",
)


def _is_progress_line(line: str) -> bool:
    """过滤 winget 下载/安装进度条,避免日志框被刷屏。"""
    stripped = line.strip()
    if not stripped:
        return True
    if set(stripped) <= set("-\\|/ ▒█▓░."):
        return True
    if re.fullmatch(r"[█▒▓░\s]+(?:\d{1,3}%|\d+(?:\.\d+)?\s*(?:KB|MB|GB)\s*/\s*\d+(?:\.\d+)?\s*(?:KB|MB|GB))", stripped):
        return True
    return False


def _is_network_failure(returncode: int, output: str) -> bool:
    """winget 下载阶段的网络不可达失败。

    GitHub 被墙/无代理时 winget 报 InternetOpenUrl() failed / 0x80072efd，
    退出码为 0x80072EFD(2147954429)。识别后给出明确的“连代理”提示，
    而不是只甩一个退出码让用户一头雾水。
    """
    if returncode in (2147954429, 2147954429 - 2**32):  # 0x80072EFD
        return True
    lowered = output.lower()
    return any(m in lowered for m in ("internetopenurl", "0x80072efd", "0x80072ee"))


def _is_msix_deploy_failure(returncode: int, output: str) -> bool:
    """MSIX/AppX 部署阶段失败。

    下载与哈希校验都通过了(日志已出现“已成功验证安装程序哈希/正在启动程序包安装”),
    随后报 0x80070002(文件找不到)或底层 0x80073CF1(Package was not found)。
    常见于精简版/LTSC、卸载了 Microsoft Store 或禁用 Windows 更新的系统:AppX 部署服务
    或框架依赖(VCLibs/UI.Xaml)不可用,winget 下载得到却无法把 MSIX 部署上去。
    """
    if returncode in (2147942402, 2147942402 - 2**32):  # 0x80070002
        return True
    lowered = output.lower()
    return any(m in lowered for m in ("0x80070002", "0x80073cf1", "package was not found"))


def _winget_no_update(returncode: int, output: str) -> bool:
    """winget 对“已安装且无升级”会返回非 0,这里把它视为已满足。"""
    if returncode not in (WINGET_NO_APPLICABLE_UPDATE, WINGET_NO_APPLICABLE_UPDATE_SIGNED):
        return False
    lowered = output.lower()
    return any(marker in lowered for marker in NO_UPDATE_MARKERS)


def _resource_candidates(relative_path: str) -> list[Path]:
    """兼容 PyInstaller 单文件、源码运行、exe 同目录资源三种情况。"""
    rel = Path(relative_path)
    bases = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)),
        Path(sys.executable).resolve().parent,
        Path.cwd(),
        Path(__file__).resolve().parent,
    ]
    candidates: list[Path] = []
    for base in bases:
        candidate = base / rel
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _resource_path(relative_path: str) -> Path:
    """返回第一个存在的资源路径；都不存在时返回 PyInstaller 临时目录下的预期路径。"""
    candidates = _resource_candidates(relative_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _prepare_bundled_exe(source_path: Path, run_name: str) -> Path:
    """
    PyInstaller 单文件包里直接内置 exe 时，部分机器会在 _MEI 临时目录误删资源。
    因此资源可以用 .bin 存放，运行前复制成真正的 .exe。
    """
    if source_path.suffix.lower() == ".exe":
        return source_path

    temp_dir = Path(tempfile.gettempdir()) / "xiaoshagua_installers"
    temp_dir.mkdir(parents=True, exist_ok=True)
    target_path = temp_dir / run_name
    shutil.copy2(source_path, target_path)
    return target_path


def _verify_claude_installed() -> tuple[bool, str]:
    """原生安装器把 claude.exe 放到 %USERPROFILE%\\.local\\bin;装完当前进程 PATH 还没刷新,直接查这个路径最可靠。"""
    local_bin = Path(os.path.expanduser("~")) / ".local" / "bin" / "claude.exe"
    if local_bin.exists():
        return True, str(local_bin)
    found = shutil.which("claude")
    if found:
        return True, found
    return False, ""


def install_stream(install_cfg: dict) -> Iterator[str]:
    """
    根据 install 配置安装,逐行 yield 日志。
    install_cfg 形如 {"type": "winget", "id": "xxx"}、{"type": "npm", "package": "xxx"}
    或 {"type": "bundled_exe", "path": "assets/xxx.exe"}
    """
    itype = install_cfg.get("type")
    if itype == "winget":
        winget = find_winget()
        if not winget:
            yield "[错误] 当前系统未检测到 winget，无法使用一键安装。"
            yield "[原因] winget 由 Microsoft App Installer 提供；常见于精简版 Windows、企业镜像、旧系统或 PATH 未刷新。"
            yield "[修复] 进入“环境冲突检测”点击“一键修复检测到的问题”，工具会优先补 WindowsApps 到 PATH，仍没有时走无商店安装 App Installer。"
            yield "[手动] 有 Microsoft Store 时可搜索“应用安装程序 / App Installer”；没有商店时请用一键修复的无商店安装。"
            yield "[备用] 也可以先手动安装 Windows Terminal、PowerShell、Git、Node.js、Python 后再运行检测。"
            return
        cmd = (
            f'"{winget}" install --id "{install_cfg["id"]}" --exact '
            f'--silent --accept-package-agreements --accept-source-agreements '
            f'--disable-interactivity'
        )
        target = install_cfg["id"]
    elif itype == "npm":
        cmd = f'npm install -g {install_cfg["package"]}'
        target = install_cfg["package"]
    elif itype == "native_script":
        target = install_cfg.get("name", "安装项")
        ps = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        script = install_cfg["script"]
        cmd = f'"{ps}" -NoProfile -ExecutionPolicy Bypass -Command "{script}"'
    elif itype == "bundled_exe":
        exe_path = _resource_path(install_cfg["path"])
        run_name = install_cfg.get("run_name") or exe_path.with_suffix(".exe").name
        target = run_name
        if not exe_path.exists():
            yield f"[错误] 找不到内置安装器: {exe_path}"
            yield "[提示] 请确认正在运行的是 230MB 左右的新版本安装包；如果仍失败，把 assets 文件夹放到本程序同目录后重试。"
            yield "[已查找] " + " | ".join(str(p) for p in _resource_candidates(install_cfg["path"]))
            return
        try:
            exe_path = _prepare_bundled_exe(exe_path, run_name)
        except OSError as e:
            yield f"[错误] 无法释放内置安装器: {e}"
            return
        args = install_cfg.get("args", "")
        cmd = f'"{exe_path}" {args}'.strip()
    else:
        yield f"[错误] 未知安装类型: {itype}"
        return
    yield f"$ {cmd}"

    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
            cwd=os.fspath(exe_path.parent) if itype == "bundled_exe" else None,
            creationflags=0 if itype == "bundled_exe" else (subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
    except OSError as e:
        yield f"[错误] 无法启动安装命令: {e}"
        return

    assert proc.stdout is not None
    output_lines: list[str] = []
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        output_lines.append(line)
        if _is_progress_line(line):
            continue
        yield line

    proc.wait()
    if itype == "native_script":
        ok, where = _verify_claude_installed()
        if ok:
            yield f"[完成] {target} 安装成功:{where}"
            yield "[提示] 请关闭并重新打开终端,使 PATH 生效后即可使用 claude 命令。"
        else:
            yield f"[失败] {target} 安装后未检测到 claude(退出码 {proc.returncode})。"
            yield "[原因] 可能无法访问 claude.ai(需代理/VPN),或脚本被网络拦截。"
            yield "[手动] 在 PowerShell 中运行:irm https://claude.ai/install.ps1 | iex"
    elif proc.returncode == 0:
        yield f"[完成] {target} 安装/更新成功"
    elif itype == "winget" and _winget_no_update(proc.returncode, "\n".join(output_lines)):
        yield f"[完成] {target} 已安装,当前没有可用更新"
    elif itype == "winget" and _is_network_failure(proc.returncode, "\n".join(output_lines)):
        yield f"[失败] {target} 无法连接下载服务器(GitHub)。"
        yield "[原因] 网络无法访问 github.com,错误码 0x80072efd;多为被墙/无代理或公司网络拦截。"
        yield "[修复] 连接代理 / VPN 后重试,或换一个能打开 github.com 的网络环境再安装。"
    elif itype == "winget" and _is_msix_deploy_failure(proc.returncode, "\n".join(output_lines)):
        yield f"[失败] {target} 已下载校验通过,但系统无法部署 MSIX 应用。错误码 0x80070002。"
        yield "[原因] 多为精简版/LTSC、卸载了 Microsoft Store 或禁用 Windows 更新,导致 AppX 部署服务/框架依赖缺失。"
        yield "[修复] 管理员 PowerShell 运行 Repair-WinGetPackageManager -Latest -Force 后重试;并确认未禁用 AppXSvc 与 Windows 更新服务。"
        yield "[备用] 也可在普通(非管理员)PowerShell 手动执行 winget install,或从 Microsoft Store / 官方发布页安装 Windows Terminal、PowerShell。"
    else:
        yield f"[失败] {target} 退出码: {proc.returncode}"


def install_batch(
    items: list[dict],
    on_log: Callable[[str], None],
    on_item_start: Callable[[dict], None] = None,
    on_item_done: Callable[[dict, bool], None] = None,
) -> None:
    """
    批量安装。items 是 software_config 中的元素列表。
    on_log:每行日志回调
    on_item_start:每个软件开始安装时回调
    on_item_done:每个软件结束时回调 (item, success)
    """
    for item in items:
        if on_item_start:
            on_item_start(item)
        on_log(f"\n=== 开始处理: {item['name']} ===")

        success = False
        for line in install_stream(item["install"]):
            on_log(line)
            if line.startswith("[完成]"):
                success = True

        if on_item_done:
            on_item_done(item, success)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    # 命令行自测:试装一个基础组件
    print("自测:试装 Windows Terminal(已安装会提示)")
    print("-" * 50)
    for line in install_stream({"type": "winget", "id": "Microsoft.WindowsTerminal"}):
        print(line)
