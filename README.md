# 小傻瓜环境配置

一个 Windows 开发环境一键配置工具，基于 PyQt6 构建图形界面。

## 功能

- 检测并安装常用开发环境：Windows Terminal、PowerShell、Git、Node.js、Python
- 可选安装 Codex、Claude 桌面版
- 配置 Claude Code、Codex、Claude 桌面版常用 API 环境变量
- 检测并修复常见 AI 工具环境冲突，例如 Key 污染、代理变量、配置文件异常、安装来源冲突

## 运行源码

```powershell
pip install -r requirements.txt
python main.py
```

建议在 Windows 上运行，并使用管理员权限启动，以便 winget 安装和环境修复功能正常工作。

## 打包

```powershell
pip install pyinstaller
pyinstaller -y "小傻瓜环境配置.spec"
```

开源版默认不包含第三方安装器文件。若需要把 Codex 或 Claude 桌面版安装器打进单文件程序，请自行准备安装器，并放到：

```text
assets/Codex Installer.bin
assets/Claude Setup.bin
```

`.bin` 文件可以由对应 `.exe` 安装器复制改名得到。第三方安装器可能有各自的许可协议，请自行确认分发权限。

## 注意

本项目不会在仓库内保存用户 API Key。运行时填写的配置会保存在当前用户的 `%APPDATA%/小傻瓜环境配置/` 目录中。
