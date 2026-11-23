# 求职邮件进度 Agent

本地个人求职邮件追踪工具。邮件经粗筛后抽取为事件；岗位当前状态由 `job_email/state_machine.py` 中的固定规则推算，事件和原文片段保留在时间线中。

## 快速开始

需要 Python 3.10 或更新版本，无第三方依赖。把邮件放入 `data/mailbox/`，支持 `.eml`、`.mbox`，随后在项目目录运行：

```powershell
python -m job_email
```

命令会生成 `data/applications.json` 并在浏览器打开本机页面。只重算数据：

```powershell
python -m job_email run
```

## 批量生成脱敏副本

预览待处理邮件（不会写文件）：

```powershell
python -m job_email redact --preview
```

生成副本（原邮件不变）：

```powershell
python -m job_email redact
```

如果要确保自己的中文姓名都被替换，追加姓名参数（可重复多个）：

```powershell
python -m job_email redact --name "张三" --name "张小三"
```

默认读取 `data/mailbox/`，写入 `data/redacted_mailbox/`，并生成 `redaction-report.json`。工具移除常见个人邮箱、手机号、网页/短链以及常见称呼中的部分中文姓名，重新生成精简邮件头并丢弃附件。姓名规则可能漏掉正文中的其他姓名；地址、薪资或图片内文字也无法可靠识别，分享/提交之前必须人工抽查副本。输入和输出目录均已加入 `.gitignore`。

只启动页面（需要先生成数据）：

```powershell
python -m job_email serve
```

默认使用内置规则抽取。需要大模型时，将 `.env.example` 复制为 `.env` 并填写 `OPENAI_API_KEY`；也可通过 `OPENAI_BASE_URL`、`OPENAI_MODEL` 指定兼容接口。邮件正文会发送至配置的模型服务。

## 邮件准备清单

建议先准备 10–30 封已经脱敏的真实邮件，尽量覆盖不同阶段；同一岗位的多封邮件请都保留，便于检查状态推进和冲突处理。

- 网申提交确认、笔试通知及笔试结果。
- 面试邀请、面试链接、面试反馈、通过/进入下一轮通知。
- 拒信或感谢信（特别需要能看出“感谢信”是否确实表示拒绝的上下文）、offer 邮件。
- 同一邮件的重复转发/重复副本，用于核对去重；有争议或表达含糊的样例也很有帮助。
- 优先导出 `.eml`（每封一份）或 `.mbox`（整批邮箱归档），保留发件人、主题、发送日期和正文。放入 `data/mailbox/`，可按公司或来源建立子文件夹。

发送前请遮盖姓名、邮箱、电话、地址、证件号、面试链接中的个人 token，以及 offer 中的薪资等隐私字段。请不要提供邮箱密码或授权令牌。邮件样例到位后，再按样例调整筛选关键词、发件域名映射和抽取规则。

配置文件为 `config.json`：`keywords` 和 `sender_domain_hints` 控制粗筛，`company_by_domain` 可将发件域名映射到公司名；置信度阈值默认为 `0.6`。

## 目录

- `job_email/ingest.py`：读取 `.eml` / `.mbox` 并粗筛。
- `job_email/extract.py`：结构化事件抽取（模型或规则兜底）。
- `job_email/merge.py`：公司/岗位归一、疑似岗位标注、同日同类型去重。
- `job_email/state_machine.py`：固定状态推算和异常标注。
- `web/`：按公司、按岗位类型视图及岗位事件时间线。
- `data/mailbox/`：本地邮件输入目录；`data/applications.json` 为生成数据。
