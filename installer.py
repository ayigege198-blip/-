"""安装引擎 - 封装 winget/npm 安装,流式输出日志"""
import os
import re
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterator


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


def install_stream(install_cfg: dict) -> Iterator[str]:
    """
    根据 install 配置安装,逐行 yield 日志。
    install_cfg 形如 {"type": "winget", "id": "xxx"}、{"type": "npm", "package": "xxx"}
    或 {"type": "bundled_exe", "path": "assets/xxx.exe"}
    """
    itype = install_cfg.get("type")
    if itype == "winget":
        cmd = (
            f'winget install --id "{install_cfg["id"]}" --exact '
            f'--silent --accept-package-agreements --accept-source-agreements '
            f'--disable-interactivity'
        )
        target = install_cfg["id"]
    elif itype == "npm":
        cmd = f'npm install -g {install_cfg["package"]}'
        target = install_cfg["package"]
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
    if proc.returncode == 0:
        yield f"[完成] {target} 安装/更新成功"
    elif itype == "winget" and _winget_no_update(proc.returncode, "\n".join(output_lines)):
        yield f"[完成] {target} 已安装,当前没有可用更新"
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
