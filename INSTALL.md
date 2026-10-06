# 在另一台 Mac 安装

## 给使用者

1. 把完整ZIP通过隔空投送或U盘传到目标Mac，解压。
2. 将其中的 WeChatgroup 放到你的“文稿”目录。若那里已有同名目录，不要覆盖，请让Codex选择新目录。
3. 在目标Mac的Codex中打开该项目文件夹，把下面的安装任务发给它。

## 可直接交给 Codex 的安装任务

请安装本目录中的微信本地总结工具和 wechat-local-report Skill，先读取本文件、README.md和Skill说明。只完成安装与验证，不在安装阶段退出微信、改签名或读取聊天。

- 检查本目录和上级适用规范。检查 uname -m、macOS版本、Python、系统LLDB和微信版本/构建号，分别报告Intel与arm64架构。已验证范围见docs/SOURCES.md，其他组合明确标记未验证，不解除源码版本保护、不降级微信或关闭系统保护。
- 推荐每台Mac克隆到非iCloud的 ~/wechat-local-report，创建独立虚拟环境与输出；helper默认引擎位置仍为 ~/Documents/WeChatgroup，安装在其他路径时每次传入 --project。若已有项目或使用其他位置，不覆盖旧文件，用helper --project传入实际绝对路径。
- 用可用Python3.10+（优先3.12）在本项目创建全新.venv，安装requirements.lock.txt。不要复用源电脑虚拟环境，不安装全局依赖；缺系统工具时说明需要用户完成的最小操作。
- 将 skills/wechat-local-report 完整复制到本机用户Skill目录。当前官方目录是 ~/.agents/skills/wechat-local-report；先检查 ~/.agents/skills 和 ~/.codex/skills 中是否已有同名Skill，避免重复安装。已存在则比较差异，不擅自覆盖；用正规权限申请完成目录写入。官方说明：https://learn.chatgpt.com/docs/build-skills 。
- 在项目目录运行 .venv/bin/python -m pytest -q 和 .venv/bin/python -m wechat_report doctor；doctor输出可能含本机账号信息，不对外分享。
- 检查Chrome或Playwright Chromium；若缺浏览器，用项目虚拟环境的 python -m playwright install chromium 安装用户级浏览器组件（正常申请所需权限）。运行 .venv/bin/python -m wechat_report demo，检查HTML与PNG、中文字体及证据展开。若有Chrome，可额外用 PYTHONPATH=. .venv/bin/python scripts/verify_render.py <demo输出目录> 验证。
- 验证Skill可发现；没有显示时重启Codex再检查。清楚区分“安装成功”“虚构测试成功”和“真实读取尚未验证”。在用户另行指定群和时间、确认必要调试授权之后，才进入真实读取流程。

## 首次使用

在新任务中输入：使用 $wechat-local-report，总结微信群“完整群名”最近一周的本地已同步聊天，生成HTML和PNG。

新电脑必须登录本人的微信，并同步所需时段的聊天记录。同名账号不代表两台电脑本地记录相同。密钥必须在目标电脑按受控流程临时获取，不能迁移、粘贴或写入配置。

本包不含聊天记录、原账号配置、数据库、密钥、微信应用副本、历史报告或源电脑虚拟环境。
