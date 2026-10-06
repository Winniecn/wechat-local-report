# wechat-local-report

Mac 微信本地群聊记录读取与 Codex 总结 Skill。包含完整读取引擎，生成带来源引用的中文总结、离线 HTML 和 PNG 长图。

## 功能与边界

- 完整群名匹配，支持群 ID 消歧、单账号读取。
- 最近24/48/72小时、最近一周、今天、昨天或指定带时区的起止时间；固定截止时刻，区间含起点、不含终点。
- 本地加密数据库一致性快照、逐页认证、有效已提交 WAL 校验与合并；临时明文尽可能清理。
- 文本、引用及可解析卡片；未解析媒体仅标记类型，不猜测内容。
- 全量分批读取后由当前 Codex 会话生成总结，重要结论关联消息 ID；无需额外模型 API Key。
- 每次运行输出 messages.json、messages.txt、report.json、summary.md、index.html、report.png；过长时 PNG 编号分图。

**export 仅导出消息，不是脱离 Codex 的独立 AI 总结服务。本地已同步记录不代表群聊完整历史。**

## 已验证兼容范围

macOS 26.5.1 / Apple Silicon arm64 / App Store 微信 4.1.13（269602）。Intel Mac、其他微信版本及其他系统组合尚未验证，不能据此承诺兼容。安装成功与虚构测试通过不等于真实微信读取成功。

## 安装

建议每台 Mac 使用非 iCloud 的独立本地项目目录，避免共享虚拟环境和私密输出。

```sh
git clone https://github.com/Winniecn/wechat-local-report.git ~/wechat-local-report
cd ~/wechat-local-report
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m wechat_report demo
```

需要 Python 3.10+（实测3.12）、系统LLDB，以及本地Chrome或Playwright Chromium。没有可用浏览器时，使用 `.venv/bin/python -m playwright install chromium` 安装渲染浏览器。

将 `skills/wechat-local-report` 完整复制到 `~/.agents/skills/wechat-local-report`。若已有同名Skill，先核对，不覆盖、不重复安装。Codex未发现时重启后检查。

默认引擎路径为 `~/Documents/WeChatgroup`。按上述路径安装时，在 Codex 中明确告诉它项目为 `~/wechat-local-report`，并在 helper 命令中指定：

```sh
python3 ~/.agents/skills/wechat-local-report/scripts/run.py --project ~/wechat-local-report engine doctor
```

可以直接让 Codex 阅读 [INSTALL.md](INSTALL.md) 并完成环境检查和本机安装。安装阶段不会授权读取聊天或修改微信。

## 使用

向 Codex 输入：

> 使用 $wechat-local-report。引擎在 ~/wechat-local-report。总结本人微信群“完整群名”最近一周的本地已同步聊天，生成中文总结、HTML和PNG。

首次真实读取可能需要暂时退出微信、使用经验证的同版本临时调试副本及手机确认，须对具体动作明确授权。原版应用不修改，不关闭SIP，不移除沙盒，不跳过版本和密码学校验。出现真正独立的 CAPTURE_READY 状态后才操作登录；提示说明中的同名词不代表就绪。

## 隐私与恢复

仅处理本人授权账号的本地数据。密钥通过受控进程管道短暂使用，不写入日志、代码、配置或报告。输出包含聊天隐私，请保存在本地，不提交Git、不自动上传公网。当前Codex会话需要接收消息内容来总结；HTML离线展示不意味着AI总结完全离线。

临时明文在正常和可控异常退出时清理；无法保证断电、SIGKILL或系统快照下安全擦除。故障时按 Skill 的 [access.md](skills/wechat-local-report/references/access.md) 恢复原版，不反复修改签名。

## 验证与来源

`tests/` 使用虚构数据；浏览器检查脚本 `scripts/verify_render.py` 目前要求Chrome在标准应用路径。测试和真实读取应分别报告。

参见 [来源与固定提交](docs/SOURCES.md)、[总结与来源核验](skills/wechat-local-report/references/reporting.md)。

## 许可证

本项目采用 [MIT](LICENSE)。参考项目 wcdb-key-tool 的 MIT 版权声明保留于 [research/LICENSE](research/LICENSE)。未确认许可证的研究代码不随本仓库分发。
