"""AI 助手配置持久化:每个 AI 的 (api_key, base_url, model, timeout) 存到 JSON。"""
import json
import os
import shutil
import subprocess
from datetime import datetime


def _config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "小傻瓜环境配置")
    os.makedirs(path, exist_ok=True)
    return path


def _config_file() -> str:
    return os.path.join(_config_dir(), "ai_config.json")


def load_all() -> dict:
    path = _config_file()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_all(data: dict) -> None:
    with open(_config_file(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_config(ai_key: str, defaults: dict | None = None) -> dict:
    data = load_all()
    cfg = data.get(ai_key, {})
    if defaults:
        merged = dict(defaults)
        merged.update({k: v for k, v in cfg.items() if v is not None})
        return merged
    return cfg


def set_config(ai_key: str, cfg: dict) -> None:
    data = load_all()
    data[ai_key] = cfg
    save_all(data)


def backup_config() -> str:
    src = _config_file()
    if not os.path.exists(src):
        raise FileNotFoundError("暂无配置可备份")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(_config_dir(), f"ai_config_backup_{ts}.json")
    shutil.copy2(src, dst)
    return dst


def restore_config(backup_path: str) -> None:
    shutil.copy2(backup_path, _config_file())


def bypass_claude_onboarding() -> str:
    """
    跳过 Claude Code 的首次登录引导。
    原理:在 ~/.claude.json 中写入 hasCompletedOnboarding=true。
    返回操作结果描述文本。
    """
    home = os.path.expanduser("~")
    path = os.path.join(home, ".claude.json")
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}
    data["hasCompletedOnboarding"] = True
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _first_existing_path(paths: list[str]) -> str:
    for raw_path in paths:
        expanded = os.path.expandvars(raw_path)
        if os.path.exists(expanded):
            return expanded
    raise FileNotFoundError("未找到可启动的程序路径")


def launch_ai(launch_target, env_map: dict, cfg: dict) -> None:
    """
    在新的 Windows Terminal(或 cmd 兜底)窗口中启动 AI CLI/桌面应用,
    并把 cfg 中的字段按 env_map 注入到环境变量。
    """
    env = os.environ.copy()
    for field, env_name in env_map.items():
        if not env_name:
            continue
        val = cfg.get(field, "")
        if val:
            env[env_name] = str(val)

    if isinstance(launch_target, dict) and launch_target.get("type") == "path":
        app_path = _first_existing_path(launch_target.get("paths", []))
        if app_path.lower().endswith(".lnk"):
            os.startfile(app_path)
            return
        subprocess.Popen([app_path], env=env)
        return

    launch_cmd = str(launch_target)
    # 优先使用 Windows Terminal,其次 PowerShell,最后 cmd
    if shutil.which("wt.exe"):
        # wt 子命令不支持复杂的环境变量注入参数,这里直接由当前进程 env 传递
        cmd = f'wt.exe cmd /k "{launch_cmd}"'
    else:
        cmd = f'start "AI CLI" cmd /k "{launch_cmd}"'

    subprocess.Popen(cmd, shell=True, env=env)
