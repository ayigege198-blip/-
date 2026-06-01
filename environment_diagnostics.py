"""环境冲突诊断:只读扫描 Claude/Codex 常见配置失败原因。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import winreg
from datetime import datetime
from pathlib import Path


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


def _run(cmd: str, timeout: int = 8) -> tuple[int, str]:
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
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


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


def _check_basic_tools(lines: list[str]) -> int:
    issues = 0
    _add(lines, "INFO", "基础环境")
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

    where = _run("where.exe claude")[1].strip().splitlines()
    where = [x.strip() for x in where if x.strip()]
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

    where = _run("where.exe codex")[1].strip().splitlines()
    where = [x.strip() for x in where if x.strip()]
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
    policy = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell") as key:
            policy = winreg.QueryValueEx(key, "ExecutionPolicy")[0]
    except OSError:
        policy = "Undefined"
    if policy not in {"Restricted", "AllSigned", "Undefined", ""}:
        return False
    code, out = _run('powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force"', timeout=20)
    if code == 0:
        _add(lines, "FIX", "已设置 PowerShell CurrentUser ExecutionPolicy = RemoteSigned")
        return True
    _add(lines, "WARN", f"设置 ExecutionPolicy 失败: {out.strip()[:160]}")
    return False


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


def run_environment_repair() -> str:
    lines: list[str] = []
    _add(lines, "INFO", "开始一键修复。会自动备份或移动可恢复文件。")
    _add(lines, "INFO", "不会静默删除看起来有效的 API Key，也不会清理代理变量；这些需要人工确认。")
    lines.append("")

    changed = 0
    for action in [
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
