"""环境冲突诊断:只读扫描 Claude/Codex 常见配置失败原因。"""
from __future__ import annotations

import base64
import json
import locale
import os
import re
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
import winreg
import ctypes
from datetime import datetime
from pathlib import Path

from detector import (
    find_winget,
    get_windowsapps_dir,
    check_winget_available,
    winget_meets_minimum,
    reset_winget_cache,
    WINGET_MIN_VERSION,
)


CLAUDE_ENV_KEYS = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_API_KEY",
]

CODEX_ENV_KEYS = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_ORGANIZATION",
    "CODEX_API_KEY",
    "AZURE_OPENAI_API_KEY",
]

PROXY_ENV_KEYS = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"]


def _decode_process_output(raw: bytes) -> str:
    encodings = [
        locale.getpreferredencoding(False),
        "utf-8-sig",
        "utf-8",
        "mbcs",
        "gbk",
        "cp936",
        "utf-16",
        "utf-16le",
    ]
    candidates: list[tuple[int, str]] = []
    for enc in encodings:
        if not enc:
            continue
        try:
            text = raw.decode(enc, errors="replace")
        except LookupError:
            continue
        bad = text.count("\ufffd")
        controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t")
        nul = text.count("\x00")
        candidates.append((bad * 100 + controls * 10 + nul * 50, text))
    if not candidates:
        return raw.decode("utf-8", errors="replace")
    return min(candidates, key=lambda item: item[0])[1]


def _run(cmd: str, timeout: int = 8) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.returncode, _decode_process_output((result.stdout or b"") + (result.stderr or b""))
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def _run_powershell(script: str, timeout: int = 60) -> tuple[int, str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    cmd = subprocess.list2cmdline([
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        encoded,
    ])
    return _run(cmd, timeout=timeout)


def _mask_value(value: str) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 10:
        return value[:2] + "***"
    return value[:6] + "***" + value[-4:]


def _looks_like_key(value: str, family: str) -> bool:
    value = value.strip()
    if not value or any(ord(ch) > 127 for ch in value) or re.search(r"\s", value):
        return False
    if family == "anthropic":
        return value.startswith("sk-ant-")
    if family == "openai":
        return value.startswith(("sk-", "sk-proj-", "sk-svcacct-"))
    return value.startswith("sk-")


def _read_text(path: Path, max_bytes: int = 512 * 1024) -> str:
    try:
        if not path.exists() or path.stat().st_size > max_bytes:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _read_json(path: Path):
    text = _read_text(path)
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(path.name + ".bak." + _timestamp())
    shutil.copy2(path, backup)
    return backup


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _backup_file(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _add(lines: list[str], level: str, text: str) -> None:
    lines.append(f"[{level}] {text}")


def _same_path(left: str | Path, right: str | Path) -> bool:
    def normalize(value: str | Path) -> str:
        text = os.path.expandvars(str(value)).strip().strip('"')
        return os.path.normcase(os.path.normpath(text))

    return normalize(left) == normalize(right)


def _path_contains(path_value: str, target: Path) -> bool:
    return any(_same_path(part, target) for part in path_value.split(os.pathsep) if part.strip())


def _where_command(name: str) -> list[str]:
    code, out = _run(f"where.exe {name}")
    if code != 0:
        return []
    bad_markers = (
        "could not find files",
        "information: could not find",
        "找不到",
        "无法找到",
        "沒有找到",
        "未找到",
        "提供的模式无法找到",
    )
    result: list[str] = []
    for raw_line in out.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(marker in lowered for marker in bad_markers):
            continue
        result.append(line)
    return result


def _broadcast_environment_change() -> bool:
    try:
        hwnd_broadcast = 0xFFFF
        wm_settingchange = 0x001A
        smto_abortifhung = 0x0002
        result = ctypes.c_ulong()
        return bool(ctypes.windll.user32.SendMessageTimeoutW(
            hwnd_broadcast,
            wm_settingchange,
            0,
            "Environment",
            smto_abortifhung,
            5000,
            ctypes.byref(result),
        ))
    except Exception:
        return False


def _check_basic_tools(lines: list[str]) -> int:
    issues = 0
    _add(lines, "INFO", "基础环境")
    winget = find_winget()
    if winget:
        code, out = _run(f'"{winget}" --version')
        if code == 0 and out.strip():
            _add(lines, "OK", f"winget: {out.strip().splitlines()[0]} ({winget})")
            windowsapps = get_windowsapps_dir()
            if not shutil.which("winget") and _same_path(Path(winget).parent, windowsapps):
                issues += 1
                _add(lines, "WARN", f"winget 已存在，但当前用户 PATH 缺少 WindowsApps: {windowsapps}")
        else:
            issues += 1
            _add(lines, "WARN", f"winget 路径存在但无法运行: {winget}")
    else:
        issues += 1
        _add(lines, "FOUND", "未检测到 winget。可在“一键修复”中尝试自动安装 Microsoft App Installer。")

    for name, cmd in [
        ("PowerShell", "powershell -NoProfile -Command \"$PSVersionTable.PSVersion.ToString()\""),
        ("Node.js", "node --version"),
        ("npm", "npm --version"),
        ("Git", "git --version"),
    ]:
        code, out = _run(cmd)
        if code == 0 and out.strip():
            _add(lines, "OK", f"{name}: {out.strip().splitlines()[0]}")
        else:
            issues += 1
            _add(lines, "WARN", f"{name}: 未检测到，可能导致 CLI 安装或运行失败")

    policy = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell") as key:
            policy = winreg.QueryValueEx(key, "ExecutionPolicy")[0]
    except OSError:
        policy = "Undefined"
    if policy in {"Restricted", "AllSigned", "Undefined", ""}:
        issues += 1
        _add(lines, "WARN", f"ExecutionPolicy(CurrentUser): {policy or '未知'}，可能阻止 npm.ps1/install.ps1")
    else:
        _add(lines, "OK", f"ExecutionPolicy(CurrentUser): {policy}")
    return issues


def _check_proxy(lines: list[str]) -> int:
    issues = 0
    _add(lines, "INFO", "代理变量")
    found = False
    for key in PROXY_ENV_KEYS:
        value = os.environ.get(key) or os.environ.get(key.lower())
        if value:
            found = True
            issues += 1 if key != "NO_PROXY" else 0
            _add(lines, "WARN", f"{key} = {value}，代理/证书劫持可能影响登录、WebSocket 或 API 连接")
    if not found:
        _add(lines, "OK", "未发现 HTTP_PROXY / HTTPS_PROXY")
    return issues


def _check_env_keys(lines: list[str]) -> int:
    issues = 0
    _add(lines, "INFO", "API Key 环境变量污染")
    for key in CLAUDE_ENV_KEYS:
        value = os.environ.get(key, "")
        if not value:
            continue
        if "KEY" in key or "TOKEN" in key:
            if _looks_like_key(value, "anthropic") or key == "CLAUDE_CODE_OAUTH_TOKEN":
                _add(lines, "OK", f"{key} 已设置: {_mask_value(value)}")
            else:
                issues += 1
                _add(lines, "FOUND", f"{key} 看起来不像 Anthropic Key/OAuth Token: {_mask_value(value)}")
        else:
            _add(lines, "WARN", f"{key} = {value}，确认是否为当前服务商地址")

    for key in CODEX_ENV_KEYS:
        value = os.environ.get(key, "")
        if not value:
            continue
        if "KEY" in key:
            if _looks_like_key(value, "openai"):
                _add(lines, "OK", f"{key} 已设置: {_mask_value(value)}")
            else:
                issues += 1
                _add(lines, "FOUND", f"{key} 看起来不像 OpenAI Key: {_mask_value(value)}")
        else:
            _add(lines, "WARN", f"{key} = {value}，确认是否和当前账号/代理匹配")
    if not any(os.environ.get(k, "") for k in CLAUDE_ENV_KEYS + CODEX_ENV_KEYS):
        _add(lines, "OK", "当前进程未发现 Claude/Codex API Key 环境变量")
    return issues


def _check_claude(lines: list[str]) -> int:
    issues = 0
    home = Path.home()
    claude_dir = home / ".claude"
    _add(lines, "INFO", "Claude Code 配置")

    where = _where_command("claude")
    if len(where) > 1:
        issues += 1
        _add(lines, "WARN", f"PATH 中发现多个 claude: {' | '.join(where)}")
    elif len(where) == 1:
        _add(lines, "OK", f"claude 命令: {where[0]}")
    else:
        _add(lines, "WARN", "未检测到 claude 命令")

    npm_path = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@anthropic-ai" / "claude-code"
    native_path = home / ".local" / "bin" / "claude.exe"
    installs = [p for p in [npm_path, native_path] if p.exists()]
    if len(installs) > 1:
        issues += 1
        _add(lines, "WARN", "检测到 Claude 多安装来源，可能版本冲突: " + " | ".join(str(p) for p in installs))
    elif installs:
        _add(lines, "OK", f"Claude 安装位置: {installs[0]}")

    settings = _read_json(claude_dir / "settings.json")
    if isinstance(settings, dict):
        env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
        model = env.get("ANTHROPIC_MODEL")
        if model:
            issues += 1
            _add(lines, "WARN", f"~/.claude/settings.json 写死 ANTHROPIC_MODEL={model}，模型过期会导致错乱")
        channel = settings.get("autoUpdatesChannel")
        if channel and channel != "stable":
            _add(lines, "WARN", f"autoUpdatesChannel={channel}，遇到回归问题时建议 stable")

    credentials = _read_json(claude_dir / ".credentials.json")
    has_oauth = isinstance(credentials, dict) and bool(
        credentials.get("claudeAiOauth") or credentials.get("oauthToken") or credentials.get("accessToken")
    )
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if has_oauth and has_api_key:
        issues += 1
        _add(lines, "FOUND", "Claude 同时存在 OAuth 登录和 API Key，API Key 可能覆盖订阅登录")

    downloads = claude_dir / "downloads"
    if downloads.exists():
        try:
            files = [p for p in downloads.iterdir() if p.is_file()]
            if files:
                issues += 1
                _add(lines, "WARN", f"~/.claude/downloads 存在 {len(files)} 个临时下载文件，可能是更新卡住残留")
        except OSError:
            pass
    return issues


def _check_codex(lines: list[str]) -> int:
    issues = 0
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    _add(lines, "INFO", "Codex 配置")
    _add(lines, "OK" if codex_home.exists() else "WARN", f"CODEX_HOME: {codex_home}")

    where = _where_command("codex")
    if len(where) > 1:
        _add(lines, "WARN", f"PATH 中发现多个 codex: {' | '.join(where)}")
    elif len(where) == 1:
        _add(lines, "OK", f"codex 命令: {where[0]}")
    else:
        _add(lines, "WARN", "未检测到 codex 命令")

    npm_codex = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai" / "codex"
    desktop_codex = list(Path(r"C:\Program Files\WindowsApps").glob("OpenAI.Codex_*")) if Path(r"C:\Program Files\WindowsApps").exists() else []
    if npm_codex.exists() and desktop_codex:
        issues += 1
        _add(lines, "FOUND", "同时存在 Codex 桌面端和 npm OpenAI Codex CLI，可能造成 PATH/版本错乱")

    if "wsl" in str(codex_home).lower() or str(codex_home).startswith("\\\\wsl"):
        issues += 1
        _add(lines, "FOUND", "CODEX_HOME 指向 WSL/UNC 路径，Windows 与 WSL 共用可能损坏 SQLite")

    config_toml = codex_home / "config.toml"
    text = _read_text(config_toml)
    if text:
        if "sandbox" in text and "workspace-write" not in text and "danger-full-access" not in text and "unelevated" not in text:
            _add(lines, "WARN", "config.toml 中 sandbox 配置不常见，若出现 1385/权限问题请检查")
    sandbox_log = codex_home / ".sandbox" / "sandbox.log"
    if sandbox_log.exists():
        try:
            size_mb = sandbox_log.stat().st_size / 1024 / 1024
            if size_mb > 10:
                issues += 1
                _add(lines, "WARN", f"sandbox.log 较大: {size_mb:.1f} MB，可能积累大量错误日志")
        except OSError:
            pass

    try:
        large_sessions = [p for p in codex_home.rglob("*.jsonl") if p.stat().st_size > 50 * 1024 * 1024]
        if large_sessions:
            issues += 1
            _add(lines, "WARN", f"发现 {len(large_sessions)} 个超过 50MB 的 Codex 会话 JSONL，可能拖慢或导致异常")
    except OSError:
        pass
    return issues


def _check_processes(lines: list[str]) -> int:
    issues = 0
    _add(lines, "INFO", "运行中进程")
    code, out = _run(
        "powershell -NoProfile -Command \"Get-Process | Where-Object { $_.ProcessName -match 'claude|codex' } | Select-Object -ExpandProperty ProcessName\"",
        timeout=8,
    )
    names = [x.strip() for x in out.splitlines() if x.strip()]
    if names:
        issues += 1
        _add(lines, "WARN", "检测到运行中的 Claude/Codex 进程，安装或更新前建议关闭: " + ", ".join(sorted(set(names))))
    else:
        _add(lines, "OK", "未发现运行中的 Claude/Codex 进程")
    return issues


def check_network(timeout: int = 6) -> tuple[bool, str]:
    """检测能否直连外网/AI 服务。任一目标有响应即视为连通。"""
    targets = [
        "https://www.google.com/generate_204",
        "https://api.anthropic.com/v1/models",
        "https://www.bing.com",
    ]
    for url in targets:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout):
                return True, "可直接访问"
        except urllib.error.HTTPError:
            return True, "可直接访问"  # 有 HTTP 响应即代表网络可达
        except (urllib.error.URLError, socket.timeout, OSError):
            continue
    return False, "受限"


def _precheck_install_or_update_winget() -> bool:
    """winget 缺失或过低时安装/升级，返回最终是否可用。"""
    winget = find_winget()
    if winget:
        # 已存在但版本过低：用 winget 自身升级 App Installer
        _run(
            f'"{winget}" upgrade --id Microsoft.DesktopAppInstaller --silent '
            "--accept-source-agreements --accept-package-agreements",
            timeout=300,
        )
    else:
        # 完全缺失：无商店离线安装（微软官方 GitHub 发布页）
        _run_powershell(_winget_offline_install_script(), timeout=600)
    reset_winget_cache()
    return bool(find_winget())


def run_environment_precheck(emit) -> tuple[bool, str]:
    """启动时的自动环境预检查：winget 版本检查 + 自动更新 + 网络检测。

    emit(str): 每检查一步向 GUI 日志输出一行。
    返回 (winget_ok, winget_version)，供主界面判断是否能一键安装 winget 项目。
    """
    emit("--- 正在执行环境预检查 ---")

    emit("正在检查 winget 版本...")
    ok, ver = check_winget_available()
    if ok and winget_meets_minimum(ver):
        emit(f"winget 版本检查通过: v{ver}")
        winget_ok, winget_ver = True, ver
    else:
        current = f"v{ver}" if ok else "未安装"
        emit(f"winget 版本过低或未安装 (当前: {current}, 需要: >= v{WINGET_MIN_VERSION})")
        emit("正在自动更新 winget...")
        emit("正在下载 winget 安装包...")
        try:
            installed = _precheck_install_or_update_winget()
        except Exception as exc:  # noqa: BLE001 - 预检查不应因异常中断
            installed = False
            emit(f"winget 自动更新出错: {exc}")
        emit("下载完成，正在安装...")
        new_ok, new_ver = check_winget_available()
        if installed and new_ok:
            emit("winget 安装成功！")
            emit(f"winget 已更新至: v{new_ver}")
            winget_ok, winget_ver = True, new_ver
        else:
            emit("winget 自动更新失败，可稍后在“环境冲突检测”里点击“一键修复”重试。")
            winget_ok = bool(new_ok)
            winget_ver = new_ver if new_ok else "未安装"

    emit("正在检测网络环境...")
    net_ok, _ = check_network()
    if net_ok:
        emit("✅ 网络环境良好，可直接访问")
    else:
        emit("⚠ 网络受限，访问 GitHub / AI 服务可能需要代理或 VPN")

    emit("✅ 环境预检查完成")
    return winget_ok, winget_ver


def run_environment_diagnostics() -> str:
    lines: list[str] = []
    _add(lines, "INFO", "开始只读诊断，不会修改任何文件或环境变量")
    total_issues = 0
    for checker in [_check_basic_tools, _check_proxy, _check_env_keys, _check_claude, _check_codex, _check_processes]:
        try:
            total_issues += checker(lines)
        except Exception as exc:  # noqa: BLE001 - GUI diagnostic should keep going.
            total_issues += 1
            _add(lines, "WARN", f"{checker.__name__} 检测失败: {exc}")
        lines.append("")

    if total_issues:
        _add(lines, "SUMMARY", f"发现 {total_issues} 个可能导致配置失败或模型错乱的风险项")
    else:
        _add(lines, "SUMMARY", "未发现明显冲突")
    return "\n".join(lines)


def _set_execution_policy(lines: list[str]) -> bool:
    code, out = _run_powershell("Get-ExecutionPolicy -Scope CurrentUser", timeout=15)
    policy = out.strip().splitlines()[0].strip() if code == 0 and out.strip() else ""
    if not policy:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell") as key:
                policy = winreg.QueryValueEx(key, "ExecutionPolicy")[0]
        except OSError:
            policy = "Undefined"
    if policy == "Undefined":
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell", 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "ExecutionPolicy", 0, winreg.REG_SZ, "RemoteSigned")
            _add(lines, "FIX", "已写入 PowerShell CurrentUser ExecutionPolicy = RemoteSigned")
            return True
        except OSError:
            pass
    if policy not in {"Restricted", "AllSigned", "Undefined", ""}:
        return False
    code, out = _run_powershell("Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force", timeout=20)
    if code == 0:
        _add(lines, "FIX", "已设置 PowerShell CurrentUser ExecutionPolicy = RemoteSigned")
        return True
    verify_code, verify_out = _run_powershell("Get-ExecutionPolicy -Scope CurrentUser", timeout=15)
    verified = verify_out.strip().splitlines()[0].strip() if verify_code == 0 and verify_out.strip() else ""
    if verified in {"RemoteSigned", "Unrestricted", "Bypass"}:
        _add(lines, "OK", f"ExecutionPolicy(CurrentUser) 已是 {verified}，忽略 PowerShell 的策略覆盖提示。")
        return False
    _add(lines, "WARN", f"设置 ExecutionPolicy 失败: {out.strip()[:160]}")
    return False


def _ensure_windowsapps_on_user_path(lines: list[str]) -> bool:
    windowsapps = get_windowsapps_dir()
    if not windowsapps.exists():
        return False

    current_process_path = os.environ.get("PATH", "")
    user_path = ""
    value_type = winreg.REG_EXPAND_SZ
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ) as key:
            try:
                raw_value, raw_type = winreg.QueryValueEx(key, "Path")
                user_path = str(raw_value)
                value_type = raw_type
            except OSError:
                user_path = ""
    except OSError:
        user_path = ""

    already_in_user_path = _path_contains(user_path, windowsapps)
    already_in_process_path = _path_contains(current_process_path, windowsapps)
    if already_in_user_path and already_in_process_path:
        return False

    changed = False
    if not already_in_user_path:
        pieces = [part for part in user_path.split(os.pathsep) if part.strip()]
        pieces.append(str(windowsapps))
        new_user_path = os.pathsep.join(pieces)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "Path", 0, value_type, new_user_path)
            _add(lines, "FIX", f"已将 WindowsApps 加入当前用户 PATH: {windowsapps}")
            changed = True
        except OSError as exc:
            _add(lines, "WARN", f"写入当前用户 PATH 失败: {exc}")
            return changed

    if not already_in_process_path:
        parts = [part for part in current_process_path.split(os.pathsep) if part.strip()]
        parts.append(str(windowsapps))
        os.environ["PATH"] = os.pathsep.join(parts)
        _add(lines, "FIX", "已刷新本程序当前进程 PATH，本次修复后可直接重新检测 winget。")
        changed = True

    if changed:
        if _broadcast_environment_change():
            _add(lines, "FIX", "已通知 Windows 刷新环境变量。")
        _add(lines, "INFO", "已修复 PATH。新打开的 PowerShell/命令行窗口会自动继承；旧窗口需要关闭重开。")
        winget = find_winget()
        if winget:
            code, out = _run(f'"{winget}" --version')
            if code == 0 and out.strip():
                _add(lines, "OK", f"winget 复检通过: {out.strip().splitlines()[0]} ({winget})")
            else:
                _add(lines, "WARN", f"PATH 已补齐，但 winget 仍无法运行: {out.strip()[:240]}")
    return changed


def _delete_env_registry_value(name: str, current_value: str, lines: list[str]) -> bool:
    changed = False
    targets = [
        (winreg.HKEY_CURRENT_USER, r"Environment", "User"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "Machine"),
    ]
    for root, subkey, label in targets:
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                try:
                    value = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    continue
                if str(value) == current_value:
                    winreg.DeleteValue(key, name)
                    _add(lines, "FIX", f"已删除 {label} 环境变量 {name}")
                    changed = True
        except OSError:
            continue
    if name in os.environ and os.environ.get(name) == current_value:
        os.environ.pop(name, None)
    return changed


def _clean_invalid_key_env(lines: list[str]) -> bool:
    changed = False
    for key in CLAUDE_ENV_KEYS:
        value = os.environ.get(key, "")
        if not value or ("KEY" not in key and "TOKEN" not in key):
            continue
        if key == "CLAUDE_CODE_OAUTH_TOKEN":
            continue
        if not _looks_like_key(value, "anthropic"):
            changed = _delete_env_registry_value(key, value, lines) or changed
    for key in CODEX_ENV_KEYS:
        value = os.environ.get(key, "")
        if not value or "KEY" not in key:
            continue
        if not _looks_like_key(value, "openai"):
            changed = _delete_env_registry_value(key, value, lines) or changed
    return changed


def _fix_claude_settings(lines: list[str]) -> bool:
    path = Path.home() / ".claude" / "settings.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        return False
    changed = False
    env = data.get("env")
    if isinstance(env, dict) and "ANTHROPIC_MODEL" in env:
        env.pop("ANTHROPIC_MODEL", None)
        changed = True
        _add(lines, "FIX", "已从 ~/.claude/settings.json 移除写死的 env.ANTHROPIC_MODEL")
    if data.get("autoUpdatesChannel") not in (None, "stable"):
        data["autoUpdatesChannel"] = "stable"
        changed = True
        _add(lines, "FIX", "已设置 Claude autoUpdatesChannel = stable")
    if changed:
        _write_json(path, data)
        _add(lines, "INFO", f"修改前已自动备份: {path}.bak.<时间戳>")
    return changed


def _move_path_to_backup(path: Path, lines: list[str], reason: str) -> bool:
    if not path.exists():
        return False
    target = path.with_name(path.name + ".bak." + _timestamp())
    try:
        shutil.move(str(path), str(target))
        _add(lines, "FIX", f"{reason}: 已移动到 {target}")
        return True
    except OSError as exc:
        _add(lines, "WARN", f"{reason}: 移动失败 {exc}")
        return False


def _clean_claude_downloads(lines: list[str]) -> bool:
    downloads = Path.home() / ".claude" / "downloads"
    try:
        has_entries = downloads.exists() and any(downloads.iterdir())
    except OSError:
        has_entries = False
    if not has_entries:
        return False
    return _move_path_to_backup(downloads, lines, "Claude 临时下载残留")


def _rotate_codex_logs_and_sessions(lines: list[str]) -> bool:
    changed = False
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    sandbox_log = codex_home / ".sandbox" / "sandbox.log"
    try:
        if sandbox_log.exists() and sandbox_log.stat().st_size > 10 * 1024 * 1024:
            changed = _move_path_to_backup(sandbox_log, lines, "Codex sandbox.log 过大") or changed
    except OSError:
        pass

    archive = codex_home / ("archive-" + _timestamp())
    moved = 0
    try:
        for path in codex_home.rglob("*.jsonl"):
            if "archive-" in str(path):
                continue
            if path.stat().st_size <= 50 * 1024 * 1024:
                continue
            archive.mkdir(parents=True, exist_ok=True)
            dest = archive / path.name
            shutil.move(str(path), str(dest))
            moved += 1
    except OSError as exc:
        _add(lines, "WARN", f"归档 Codex 大会话文件失败: {exc}")
    if moved:
        changed = True
        _add(lines, "FIX", f"已归档 {moved} 个超过 50MB 的 Codex 会话 JSONL 到 {archive}")
    return changed


def _kill_ai_processes(lines: list[str]) -> bool:
    code, out = _run(
        "powershell -NoProfile -ExecutionPolicy Bypass -Command \"Get-Process | Where-Object { $_.ProcessName -match 'claude|codex' } | Stop-Process -Force -ErrorAction SilentlyContinue\"",
        timeout=15,
    )
    if code == 0:
        _add(lines, "FIX", "已尝试关闭运行中的 Claude/Codex 进程，释放安装/更新文件锁")
        return True
    _add(lines, "WARN", f"关闭 Claude/Codex 进程失败: {out.strip()[:160]}")
    return False


def _uninstall_npm_codex_if_desktop_exists(lines: list[str]) -> bool:
    npm_codex = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai" / "codex"
    desktop_root = Path(r"C:\Program Files\WindowsApps")
    desktop_codex = list(desktop_root.glob("OpenAI.Codex_*")) if desktop_root.exists() else []
    if not npm_codex.exists() or not desktop_codex:
        return False
    code, out = _run("npm uninstall -g @openai/codex", timeout=120)
    if code == 0:
        _add(lines, "FIX", "已卸载 npm 全局 @openai/codex，保留 Codex 桌面端，避免 PATH/版本错乱")
        return True
    _add(lines, "WARN", f"卸载 npm @openai/codex 失败: {out.strip()[:240]}")
    return False


def _register_existing_desktop_app_installer(lines: list[str]) -> bool:
    if find_winget():
        return False
    ps = r'''
$ErrorActionPreference = "SilentlyContinue"
$root = "C:\Program Files\WindowsApps"
$manifest = Get-ChildItem -Path $root -Filter "AppxManifest.xml" -Recurse |
    Where-Object { $_.FullName -like "*Microsoft.DesktopAppInstaller_*_8wekyb3d8bbwe*" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($manifest) {
    Add-AppxPackage -DisableDevelopmentMode -Register $manifest.FullName
    Write-Output $manifest.FullName
    exit 0
}
exit 2
'''
    code, out = _run_powershell(ps, timeout=90)
    if code == 0 and find_winget():
        _add(lines, "FIX", "已重新注册现有 Microsoft App Installer，winget 已可用。")
        return True
    if out.strip():
        _add(lines, "INFO", f"未能通过重新注册恢复 winget: {out.strip()[:300]}")
    return False


def _winget_offline_install_script() -> str:
    return r'''
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Write-Step([string]$Message) {
    Write-Output "[STEP] $Message"
}

function Get-WingetPath {
    $cmd = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $local = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe"
    if (Test-Path $local) { return $local }
    $wa = "C:\Program Files\WindowsApps"
    if (Test-Path $wa) {
        $found = Get-ChildItem -Path $wa -Filter winget.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -like "*Microsoft.DesktopAppInstaller_*" } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

if (Get-WingetPath) {
    Write-Step "winget 已存在，跳过无商店安装。"
    exit 0
}

$arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
if ($env:PROCESSOR_ARCHITECTURE -match "ARM64") { $arch = "arm64" }
$dir = Join-Path $env:TEMP "xiaoshagua-winget-offline"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$depsZip = Join-Path $dir "DesktopAppInstaller_Dependencies.zip"
$bundle = Join-Path $dir "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle"
$xaml = Join-Path $dir "Microsoft.UI.Xaml.2.8.$arch.appx"

Write-Step "下载 winget 依赖包。"
Invoke-WebRequest -Uri "https://github.com/microsoft/winget-cli/releases/latest/download/DesktopAppInstaller_Dependencies.zip" -OutFile $depsZip

Write-Step "下载 Microsoft.UI.Xaml 2.8。"
Invoke-WebRequest -Uri "https://github.com/microsoft/microsoft-ui-xaml/releases/download/v2.8.6/Microsoft.UI.Xaml.2.8.$arch.appx" -OutFile $xaml

Write-Step "下载 Microsoft Desktop App Installer。"
Invoke-WebRequest -Uri "https://github.com/microsoft/winget-cli/releases/latest/download/Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle" -OutFile $bundle

$extract = Join-Path $dir "deps"
if (Test-Path $extract) { Remove-Item -Path $extract -Recurse -Force }
Expand-Archive -Path $depsZip -DestinationPath $extract -Force

$depPaths = @()
$archDir = Join-Path $extract $arch
if (Test-Path $archDir) {
    $depPaths += Get-ChildItem -Path $archDir -Filter "*.appx" -File | Sort-Object Name | ForEach-Object { $_.FullName }
}
$depPaths += $xaml

foreach ($dep in $depPaths) {
    if (Test-Path $dep) {
        Write-Step "安装依赖: $(Split-Path $dep -Leaf)"
        Add-AppxPackage -Path $dep -ErrorAction SilentlyContinue
    }
}

Write-Step "安装 Microsoft App Installer 主包。"
Add-AppxPackage -Path $bundle

$winget = Get-WingetPath
if (-not $winget) {
    throw "安装完成后仍未找到 winget.exe。可能是系统禁用了 AppX/MSIX、版本过旧，或企业策略阻止安装。"
}

Write-Step "winget 已安装: $winget"
& $winget --version
'''


def _install_winget_app_installer(lines: list[str]) -> bool:
    if find_winget():
        return False

    _add(lines, "INFO", "未检测到 winget，开始尝试无商店安装 Microsoft App Installer。")
    _add(lines, "INFO", "会从微软 GitHub 官方发布页下载 winget 主包、依赖包和 Microsoft.UI.Xaml，不依赖 Microsoft Store。")
    code, out = _run_powershell(_winget_offline_install_script(), timeout=600)
    if code == 0 and find_winget():
        _add(lines, "FIX", "已完成无商店安装 Microsoft App Installer，winget 已可用。")
        if out.strip():
            _add(lines, "INFO", out.strip()[-800:])
        return True

    _add(lines, "WARN", "自动安装 winget 失败。")
    if out.strip():
        _add(lines, "WARN", out.strip()[-1200:])
    _add(lines, "INFO", "如果这台电脑没有 Microsoft Store，新版已走无商店安装；仍失败通常是系统禁用 AppX/MSIX、版本过旧，或企业策略阻止。")
    _add(lines, "INFO", "手动兜底：在浏览器打开 https://github.com/microsoft/winget-cli/releases/latest ，下载 DesktopAppInstaller_Dependencies.zip 和 Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle 后以管理员 PowerShell 安装。")
    return False


def _fix_winget(lines: list[str]) -> bool:
    """winget 缺失/不可用时的修复组合：补 PATH → 重新注册 → 无商店安装 → 再补 PATH。"""
    changed = False
    for action in [
        _ensure_windowsapps_on_user_path,
        _register_existing_desktop_app_installer,
        _install_winget_app_installer,
        _ensure_windowsapps_on_user_path,
    ]:
        try:
            if action(lines):
                changed = True
        except Exception as exc:  # noqa: BLE001 - repair should keep going.
            _add(lines, "WARN", f"{action.__name__} 执行失败: {exc}")
    return changed


def _detect_winget() -> bool:
    winget = find_winget()
    if not winget:
        return True
    windowsapps = get_windowsapps_dir()
    try:
        on_path = bool(shutil.which("winget"))
    except OSError:
        on_path = True
    return not on_path and _same_path(Path(winget).parent, windowsapps)


def _detect_execution_policy() -> bool:
    code, out = _run_powershell("Get-ExecutionPolicy -Scope CurrentUser", timeout=15)
    policy = out.strip().splitlines()[0].strip() if code == 0 and out.strip() else ""
    if not policy:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell") as key:
                policy = winreg.QueryValueEx(key, "ExecutionPolicy")[0]
        except OSError:
            policy = "Undefined"
    return policy in {"Restricted", "AllSigned", "Undefined", ""}


def _detect_invalid_key_env() -> bool:
    for key in CLAUDE_ENV_KEYS:
        value = os.environ.get(key, "")
        if not value or ("KEY" not in key and "TOKEN" not in key) or key == "CLAUDE_CODE_OAUTH_TOKEN":
            continue
        if not _looks_like_key(value, "anthropic"):
            return True
    for key in CODEX_ENV_KEYS:
        value = os.environ.get(key, "")
        if not value or "KEY" not in key:
            continue
        if not _looks_like_key(value, "openai"):
            return True
    return False


def _detect_claude_settings() -> bool:
    data = _read_json(Path.home() / ".claude" / "settings.json")
    if not isinstance(data, dict):
        return False
    env = data.get("env")
    if isinstance(env, dict) and "ANTHROPIC_MODEL" in env:
        return True
    return data.get("autoUpdatesChannel") not in (None, "stable")


def _detect_claude_downloads() -> bool:
    downloads = Path.home() / ".claude" / "downloads"
    try:
        return downloads.exists() and any(downloads.iterdir())
    except OSError:
        return False


def _detect_codex_logs() -> bool:
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    sandbox_log = codex_home / ".sandbox" / "sandbox.log"
    try:
        if sandbox_log.exists() and sandbox_log.stat().st_size > 10 * 1024 * 1024:
            return True
    except OSError:
        pass
    try:
        for path in codex_home.rglob("*.jsonl"):
            if "archive-" in str(path):
                continue
            if path.stat().st_size > 50 * 1024 * 1024:
                return True
    except OSError:
        pass
    return False


def _detect_npm_codex_conflict() -> bool:
    npm_codex = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai" / "codex"
    desktop_root = Path(r"C:\Program Files\WindowsApps")
    desktop_codex = list(desktop_root.glob("OpenAI.Codex_*")) if desktop_root.exists() else []
    return npm_codex.exists() and bool(desktop_codex)


def _detect_ai_processes() -> bool:
    code, out = _run(
        "powershell -NoProfile -Command \"Get-Process | Where-Object { $_.ProcessName -match 'claude|codex' } | Select-Object -ExpandProperty ProcessName\"",
        timeout=8,
    )
    return bool([x for x in out.splitlines() if x.strip()])


_REPAIR_REGISTRY = [
    {"key": "winget", "label": "安装/修复 winget（Microsoft App Installer）并补全 PATH", "detect": _detect_winget, "fix": _fix_winget},
    {"key": "exec_policy", "label": "修复 PowerShell 执行策略（ExecutionPolicy）", "detect": _detect_execution_policy, "fix": _set_execution_policy},
    {"key": "invalid_keys", "label": "清除无效的 API Key 环境变量", "detect": _detect_invalid_key_env, "fix": _clean_invalid_key_env},
    {"key": "claude_settings", "label": "修复 ~/.claude/settings.json（写死模型 / 更新通道）", "detect": _detect_claude_settings, "fix": _fix_claude_settings},
    {"key": "claude_downloads", "label": "清理 Claude 临时下载残留", "detect": _detect_claude_downloads, "fix": _clean_claude_downloads},
    {"key": "codex_logs", "label": "归档过大的 Codex 日志与会话文件", "detect": _detect_codex_logs, "fix": _rotate_codex_logs_and_sessions},
    {"key": "npm_codex", "label": "卸载与桌面端冲突的 npm Codex CLI", "detect": _detect_npm_codex_conflict, "fix": _uninstall_npm_codex_if_desktop_exists},
    {"key": "ai_processes", "label": "关闭运行中的 Claude/Codex 进程", "detect": _detect_ai_processes, "fix": _kill_ai_processes},
]


def scan_repairable_issues() -> list[dict]:
    """扫描当前存在、且可自动修复的冲突项，返回 [{key, label}]，供界面弹窗勾选。"""
    issues: list[dict] = []
    for entry in _REPAIR_REGISTRY:
        try:
            if entry["detect"]():
                issues.append({"key": entry["key"], "label": entry["label"]})
        except Exception:  # noqa: BLE001 - 单项检测失败不影响其它项
            continue
    return issues


def run_selected_repairs(keys) -> str:
    """只执行用户在弹窗中勾选的修复项，最后追加一次只读诊断。"""
    selected = set(keys or [])
    lines: list[str] = []
    _add(lines, "INFO", "开始按所选项修复。会自动备份或移动可恢复文件。")
    _add(lines, "INFO", "不会静默删除看起来有效的 API Key，也不会清理代理变量；这些需要人工确认。")
    lines.append("")

    changed = 0
    for entry in _REPAIR_REGISTRY:
        if entry["key"] not in selected:
            continue
        try:
            if entry["fix"](lines):
                changed += 1
        except Exception as exc:  # noqa: BLE001 - repair should keep going.
            _add(lines, "WARN", f"{entry['label']} 执行失败: {exc}")
    lines.append("")
    if changed:
        _add(lines, "SUMMARY", f"修复完成，执行了 {changed} 类修复。建议关闭本程序后重新打开 PowerShell/终端再测试。")
    else:
        _add(lines, "SUMMARY", "所选项目没有需要实际修改的内容，或剩余项目需要手动确认。")
    lines.append("")
    lines.append(run_environment_diagnostics())
    return "\n".join(lines)


def run_environment_repair() -> str:
    lines: list[str] = []
    _add(lines, "INFO", "开始一键修复。会自动备份或移动可恢复文件。")
    _add(lines, "INFO", "不会静默删除看起来有效的 API Key，也不会清理代理变量；这些需要人工确认。")
    lines.append("")

    changed = 0
    for action in [
        _ensure_windowsapps_on_user_path,
        _register_existing_desktop_app_installer,
        _install_winget_app_installer,
        _ensure_windowsapps_on_user_path,
        _set_execution_policy,
        _clean_invalid_key_env,
        _fix_claude_settings,
        _clean_claude_downloads,
        _rotate_codex_logs_and_sessions,
        _uninstall_npm_codex_if_desktop_exists,
        _kill_ai_processes,
    ]:
        try:
            if action(lines):
                changed += 1
        except Exception as exc:  # noqa: BLE001 - repair should keep going.
            _add(lines, "WARN", f"{action.__name__} 执行失败: {exc}")
    lines.append("")
    if changed:
        _add(lines, "SUMMARY", f"自动修复完成，执行了 {changed} 类修复。建议关闭本程序后重新打开 PowerShell/终端再测试。")
    else:
        _add(lines, "SUMMARY", "没有发现可自动修复的项目，或剩余项目需要手动确认。")
    lines.append("")
    lines.append(run_environment_diagnostics())
    return "\n".join(lines)
