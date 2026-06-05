"""AI 助手配置持久化:每个 AI 的 (api_key, base_url, model, timeout) 存到 JSON。"""
import ctypes
import json
import os
import shutil
import subprocess
import winreg
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


_CLAUDE_POLICY_KEY = r"SOFTWARE\Policies\Claude"


def _normalize_gateway_base_url(url: str) -> str:
    """Gateway base URL 规范化:去掉结尾的 / 和 /v1(桌面端会自动追加 /v1/messages 等)。"""
    url = (url or "").strip().rstrip("/")
    if url.lower().endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url


def bypass_claude_desktop_login(base_url: str, api_key: str) -> str:
    """让 Claude 桌面版跳过登录:写入开发者模式的 Gateway 托管配置。

    原理:把 Connection=Gateway 的第三方推理配置写到注册表托管位置
    HKCU\\SOFTWARE\\Policies\\Claude。其中 disableDeploymentModeChooser=true 让桌面端
    启动直接进入第三方推理模式、跳过登录/模式选择页。托管配置优先级高于应用内本地配置,
    且不受 MSIX 沙箱文件虚拟化影响,等价于在应用里手动配置后点 Export → .reg。
    修改仅在完全退出并重新打开 Claude 桌面版后生效。返回写入的注册表路径。
    """
    base_url = _normalize_gateway_base_url(base_url)
    if not base_url or not api_key:
        raise ValueError("请先在上方填写 Claude 桌面版的 API 地址和 API Key")
    values = {
        "inferenceProvider": "gateway",
        "inferenceGatewayBaseUrl": base_url,
        "inferenceGatewayApiKey": api_key,
        "inferenceGatewayAuthScheme": "bearer",
        "disableDeploymentModeChooser": "true",
    }
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, _CLAUDE_POLICY_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        for name, val in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)
    return r"HKEY_CURRENT_USER\SOFTWARE\Policies\Claude"


_ENV_REG_PATH = r"Environment"  # HKCU\Environment 用户环境变量
_MACHINE_ENV_REG_PATH = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


def _broadcast_env_change() -> None:
    """广播 WM_SETTINGCHANGE,让之后新开的进程能读到刚写入的用户环境变量。"""
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            SMTO_ABORTIFHUNG, 5000, ctypes.byref(result),
        )
    except Exception:
        pass


def _set_user_env_var(name: str, value: str) -> None:
    """写入 HKCU\\Environment 持久化用户环境变量,并同步到当前进程。"""
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, _ENV_REG_PATH, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
    os.environ[name] = str(value)


def _delete_user_env_var(name: str) -> bool:
    """从 HKCU\\Environment 删除用户环境变量。返回是否实际删除。"""
    try:
        with winreg.OpenKeyEx(
            winreg.HKEY_CURRENT_USER, _ENV_REG_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, name)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _delete_machine_env_var(name: str) -> bool:
    """尝试从 HKLM 系统环境变量删除(可能因权限失败,忽略错误)。"""
    try:
        with winreg.OpenKeyEx(
            winreg.HKEY_LOCAL_MACHINE, _MACHINE_ENV_REG_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, name)
            return True
    except (FileNotFoundError, PermissionError, OSError):
        return False


def apply_claude_code_config(cfg: dict) -> list[str]:
    """强制把 Claude Code CLI 配置写到其它软件可能读取/覆盖的所有位置,
    确保不被其它配置工具影响、强制生效。

    写入三处并清理冲突项:
      1. ~/.claude/settings.json 的 env 块(官方推荐的持久化配置位置,会自动备份)
      2. HKCU\\Environment 持久化用户环境变量(优先级高于 settings.json,且广播生效)
      3. ~/.claude.json 的 hasCompletedOnboarding=true(规避全新安装首启引导 bug)
    清理:删除会与 AUTH_TOKEN 冲突的 ANTHROPIC_API_KEY(用户/系统/当前进程三处)。

    返回执行过的动作描述列表,供界面日志展示。
    """
    base_url = (cfg.get("base_url") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip()
    timeout = str(cfg.get("timeout") or "").strip()

    if not base_url or not api_key:
        raise ValueError("请先填写 Claude Code 的 API 地址和 API Key")

    env_values = {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_AUTH_TOKEN": api_key,
    }
    if model:
        env_values["ANTHROPIC_MODEL"] = model
    if timeout:
        env_values["API_TIMEOUT_MS"] = timeout

    actions: list[str] = []

    # ---- 1. ~/.claude/settings.json 的 env 块 ----
    home = os.path.expanduser("~")
    claude_dir = os.path.join(home, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    settings_path = os.path.join(claude_dir, "settings.json")

    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
            shutil.copy2(settings_path, settings_path + ".xsg.bak")
        except (OSError, json.JSONDecodeError):
            settings = {}
    if not isinstance(settings, dict):
        settings = {}

    env_block = settings.get("env")
    if not isinstance(env_block, dict):
        env_block = {}
    env_block.update(env_values)
    env_block.pop("ANTHROPIC_API_KEY", None)  # 与 AUTH_TOKEN 冲突,清掉
    settings["env"] = env_block

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    actions.append(f"已写入 {settings_path} 的 env 配置")

    # ---- 2. HKCU\Environment 持久化用户环境变量(优先级高于 settings.json)----
    for name, val in env_values.items():
        _set_user_env_var(name, val)
    actions.append("已写入持久化用户环境变量 (HKCU\\Environment)")

    # ---- 3. 清理会冲突的 ANTHROPIC_API_KEY ----
    removed = []
    if _delete_user_env_var("ANTHROPIC_API_KEY"):
        removed.append("用户")
    if _delete_machine_env_var("ANTHROPIC_API_KEY"):
        removed.append("系统")
    if os.environ.pop("ANTHROPIC_API_KEY", None) is not None:
        removed.append("当前进程")
    if removed:
        actions.append("已清除冲突变量 ANTHROPIC_API_KEY(" + "、".join(removed) + ")")

    _broadcast_env_change()

    # ---- 4. ~/.claude.json hasCompletedOnboarding ----
    try:
        onboarding_path = bypass_claude_onboarding()
        actions.append(f"已写入 {onboarding_path}(跳过首启引导)")
    except Exception:
        pass

    return actions


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
