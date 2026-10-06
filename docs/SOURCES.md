# 来源与兼容边界

参考项目：https://github.com/TANGandXUE/wcdb-key-tool
固定提交：79f1b5b92e12c66aa281b4a60a3c478b5f547dfa。
MIT 许可证及归属保留在 research/LICENSE；本包未包含审阅时下载的第三方源码。
参考文件：该提交下 wcdb_key_tool_macos.py。项目独立实现逐页认证、WAL校验与临时快照管理；未采用保存密钥文件、打印密钥或忽略WAL的行为。

格式依据：https://www.sqlite.org/fileformat2.html 与 https://www.zetetic.net/sqlcipher/design/ 。

结构研究参考 punk2898/wechat-group-stats，提交1987714622fac1b23923135c0b029d2447cc3395。未确认许可证，不复用或分发其实现；实际必需字段运行时校验。
签名组合研究参考 Wuvomi/WeChatMulti，提交ee27e6ec789aa567e5718fc80ef1b43f06052f96 的 engine/install-clone.sh。未确认许可证，未复制、执行或作为依赖引入其代码。

实测组合为 macOS26.5.1 / arm64 / App Store微信4.1.13（269602）。其他组合未验证，不能由相同账号或同为Mac推断兼容。Intel Mac Pro需要另外验证。历史实测不代表目标电脑已读取成功。
