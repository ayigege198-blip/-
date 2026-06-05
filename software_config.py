"""软件配置数据

detect.type:
  - command: 执行命令检测,根据返回码和输出判断
  - winget:  通过 winget list 检测
  - paths:   检测本机常见安装路径
  - windowsapps: 检测 WindowsApps/MSIX 应用

install.type:
  - winget: 用 winget install
  - npm:    用 npm i -g <package>
  - bundled_exe: 运行打包内置的 exe 安装器

ai.env_vars: 环境变量名列表(用于快速启动时注入)
ai.launch_cmd: 启动命令(在新终端中执行)
"""

# ========== Tab 1: 通用工具与程序 ==========

CORE_STACK = [
    {
        "key": "windows_terminal",
        "name": "Windows Terminal",
        "detect": {"type": "winget", "id": "Microsoft.WindowsTerminal"},
        "install": {"type": "winget", "id": "Microsoft.WindowsTerminal"},
    },
    {
        "key": "powershell",
        "name": "Microsoft PowerShell",
        "detect": {"type": "command", "cmd": "pwsh --version"},
        "install": {"type": "winget", "id": "Microsoft.PowerShell"},
    },
    {
        "key": "git",
        "name": "Git for Windows",
        "detect": {"type": "command", "cmd": "git --version"},
        "install": {"type": "winget", "id": "Git.Git"},
    },
    {
        "key": "nodejs",
        "name": "Node.js LTS",
        "detect": {"type": "command", "cmd": "node --version"},
        "install": {"type": "winget", "id": "OpenJS.NodeJS.LTS"},
    },
    {
        "key": "python312",
        "name": "Python 3.12",
        "detect": {"type": "command", "cmd": "python --version"},
        "install": {"type": "winget", "id": "Python.Python.3.12"},
    },
]

EXTERNAL_TOOLS = [
    {
        "key": "codex_desktop",
        "name": "Codex",
        "desc": "OpenAI Codex 桌面端安装器",
        "detect": {
            "type": "windowsapps",
            "pattern": "OpenAI.Codex_*",
            "version_regex": r"OpenAI\.Codex_([^_]+)_",
        },
        "install": {"type": "bundled_exe", "path": "assets/Codex Installer.bin", "run_name": "Codex Installer.exe"},
    },
    {
        "key": "claude_desktop",
        "name": "Claude 桌面版",
        "desc": "Anthropic Claude 桌面应用安装器",
        "detect": {
            "type": "paths",
            "paths": [
                r"%LOCALAPPDATA%\Programs\Claude\Claude.exe",
                r"%LOCALAPPDATA%\AnthropicClaude\Claude.exe",
                r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Claude.lnk",
                r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Anthropic\Claude.lnk",
            ],
        },
        "install": {"type": "bundled_exe", "path": "assets/Claude Setup.bin", "run_name": "Claude Setup.exe"},
    },
]

# ========== Tab 2: AI 助手一键配置 ==========
# env_map: 配置字段名 -> 环境变量名
# defaults: 该 AI 的默认配置(展示给新用户)

AI_TOOLS = [
    {
        "key": "claude_code",
        "name": "Claude Code",
        "desc": "Anthropic 官方 CLI 编码助手",
        "detect": {"type": "command", "cmd": "claude --version"},
        "install": {
            "type": "native_script",
            "name": "Claude Code",
            "script": "try { npm uninstall -g @anthropic-ai/claude-code } catch {}; irm https://claude.ai/install.ps1 | iex",
        },
        "env_map": {
            "api_key": "ANTHROPIC_AUTH_TOKEN",
            "base_url": "ANTHROPIC_BASE_URL",
            "model": "ANTHROPIC_MODEL",
            "timeout": "API_TIMEOUT_MS",
        },
        "defaults": {
            "base_url": "https://api.anthropic.com",
            "model": "claude-opus-4-7",
            "timeout": "3000000",
        },
        "launch_cmd": "claude",
    },
    {
        "key": "codex_config",
        "name": "Codex",
        "desc": "OpenAI Codex 桌面端 / OpenAI API 配置",
        "detect": {
            "type": "windowsapps",
            "pattern": "OpenAI.Codex_*",
            "version_regex": r"OpenAI\.Codex_([^_]+)_",
        },
        "install": {"type": "bundled_exe", "path": "assets/Codex Installer.bin", "run_name": "Codex Installer.exe"},
        "env_map": {
            "api_key": "OPENAI_API_KEY",
            "base_url": "OPENAI_BASE_URL",
            "model": "OPENAI_MODEL",
            "timeout": "",
        },
        "defaults": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-5",
            "timeout": "",
        },
        "launch_cmd": "codex",
    },
    {
        "key": "claude_desktop_config",
        "name": "Claude 桌面版",
        "desc": "Claude 桌面端 / Anthropic API 配置",
        "detect": {
            "type": "paths",
            "paths": [
                r"%LOCALAPPDATA%\Programs\Claude\Claude.exe",
                r"%LOCALAPPDATA%\AnthropicClaude\Claude.exe",
                r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Claude.lnk",
                r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Anthropic\Claude.lnk",
            ],
        },
        "install": {"type": "bundled_exe", "path": "assets/Claude Setup.bin", "run_name": "Claude Setup.exe"},
        "env_map": {
            "api_key": "ANTHROPIC_AUTH_TOKEN",
            "base_url": "ANTHROPIC_BASE_URL",
            "model": "ANTHROPIC_MODEL",
            "timeout": "API_TIMEOUT_MS",
        },
        "defaults": {
            "base_url": "https://api.anthropic.com",
            "model": "claude-opus-4-7",
            "timeout": "3000000",
        },
        "launch": {
            "type": "path",
            "paths": [
                r"%LOCALAPPDATA%\Programs\Claude\Claude.exe",
                r"%LOCALAPPDATA%\AnthropicClaude\Claude.exe",
                r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Claude.lnk",
                r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Anthropic\Claude.lnk",
            ],
        },
    },
]
