# 中断作业恢复、断点续传与限流处理

遇到网络抖动、超时、限流、下载断片或提交状态不明时，先查已有回执、原账和本地文件，把当前所处阶段摸清楚再动手。恢复优先复用已经生成的产物和原预约，避免重复扣费。

## 第一步：按现场证据选下一步动作

先收集已有 `request`、作业目录日志、`modeling-report.json`、原账 `ledger_file`、本地文件与 SHA256，对照下表直接进入对应动作：

| 手头证据 | 立刻做什么 | 停下来核查的信号 |
|---|---|---|
| 已有完整本地 GLB 或 FBX | 算 SHA256，直接进 [本地导入](blender-handoff.md) | 文件损坏或来源不明，先回查原下载回执 |
| 已有候选 `.blend` 与报告 | 新进程冷重开候选，继续整理 | 文件 SHA 变动，先重新体检再改 |
| 本地作业仍在跑 | 用原 `--jobs-dir` 和 `job_id` 查状态或有界等待 | 仍在运行就等本次等待返回，先做其他独立环节 |
| 已知远端 `task_id` | 对原 `task_id` 跑 `query`，成功后再 `download` | 远端显示失败就保留原任务记录并调整方案 |
| 提交超时、无 `task_id` | 查原请求、原账与控制台/受理回执，核实后走 `bind_unknown` 或 `resume_pre_submit` | 尚无确凿证据时保留 `unknown` 预约，先取证 |
| 下载写到一半中断 | 确认旧作业已停，查残片和检查点文件，新作业跑 `resume_download` | 长度、强 ETag 或资源路径变动，改按新完整下载或汇报 |

## 查询与等待已有本地作业

查询已有作业时，沿用提交时的发行开关、原 `--jobs-dir` 和原 `job_id`。本地 `job_id` 管本地作业状态，远端 `task_id` 管云端任务状态，两者分开查。

```powershell
$ctl = './blenderctl.ps1'

& $ctl --modeling-release 0.1.14 --jobs-dir $origJobsDir job status $jobId
& $ctl --modeling-release 0.1.14 --jobs-dir $origJobsDir job wait $jobId --wait-seconds 30
& $ctl --modeling-release 0.1.14 --jobs-dir $origJobsDir job result $jobId
```

拿到终态后先看退出码和 `ok`，再顺着 `artifacts` 和 `modeling-report.json` 找产物路径或错误诊断。

## 查询已知任务与批量查询

- **单个已知任务**：先查 `modeling describe --operation query --schema`，用原 `ledger_file`、`credential_ref` 和 `task_id` 查询最新状态与结算回执。
- **多个已知任务（`query_batch`）**：单批放 `1–16` 个唯一 `task_id`，顺序每项查一次 GET。遇远端运行中或失败会记下快照继续查下一项；遇网络错误、限流或超时会停批并把未查项写进 `query-batch.json` 的 `remaining_task_ids`。旧作业停稳后，新作业只放 `remaining_task_ids` 接着查。

## 下载断点续传与链接刷新

### 断点续传（`resume_download`）

1. 先确认原下载作业已到达终态（或原进程已退出），保护磁盘上的残片（`partial`）和同目录检查点（`checkpoint`）原样不动。
2. 先查 `modeling describe --operation resume_download --schema`，在新作业目录构造 manifest，传入同一 `task_id`、`credential_ref`、原 `ledger_file`，以及 `recovery_descriptor` 和 `partial_descriptor` 的真实绝对路径与 SHA256。
3. 作业会校验残片 SHA、检查点已确认前缀 SHA、文件总长度、HTTPS 来源路径摘要和强 ETag，向当前下载链接请求剩余字节（HTTP 206 `Content-Range`），并把已确认前缀复制到新目录拼接，完整通过 GLB 校验后才晋升输出。
4. 若检查点显示全部字节已落盘且尚未晋升，作业会直接在本地校验并晋升为完整 GLB，省去网络重下。为整作业预留包含本地校验与大文件复制在内的充足超时时间。

### 下载链接失效或访问被拒

模型下载返回 HTTP 401/403/404/410 时，CLI 报 `ARTIFACT_UNAVAILABLE`（退出码 `13`）。确认旧作业已停后：

- **没有可用残片**：新建一个同一 `task_id` 的 `download` 作业，由工具内部重新拉取当前有效下载链接并完整下载。
- **已有完整残片和检查点**：新建 `resume_download` 作业，工具同样会先取当前链接再核对来源路径、强 ETag 和区间长度续传。
- **远端明确回报 `expired`**：停止刷新链接，检查本地已有副本或核实平台保留期限，再决定是否另起方案。

## 有据绑定未知提交（`bind_unknown`）

当收费提交因超时进入 `unknown`、本地账本缺少 `task_id`，但你在服务商控制台或找回的受理回执里确认到了对应任务，用 `bind_unknown` 把真实 `task_id` 挂回原预约，零额外扣费、零重复 POST。

1. 先查 `modeling describe --operation bind_unknown --schema` 和 `--operation binding_statement --schema`。
2. 准备绑定声明（`binding_statement`）：填入原批准 SHA256、当前账本 SHA256、`attempt_id`、`operation_id`、`operation`、`request_sha256`、已核实的 `task_id`，以及来源为 `provider_console` 或 `recovered_acceptance_receipt` 的本地证据文件描述符。
3. 提交 `bind_unknown`：工具校对身份与预算后，发一次 GET 验证任务匹配，把原账尝试更新为 `confirmed` 并保留原预约。随后按 `followup_query_required` 跑一次 `query` 完成终态费用结算。

## 提交前上传失败的单次恢复（`resume_pre_submit`）

中国站原图上传采用 `/files/presign` 预签名加 16 KiB 分块 PUT（单图 30 秒空闲、180 秒总时限）；提交两张图时，外层公共作业超时要覆盖全部上传时间（两图留足 360 秒以上）外加后续 API 调用时间。

如果作业在原图上传阶段中断，且已核实旧作业进程已停、生成 POST 尚未发出、原账尝试仍处于 `reserved` 且无 `task_id`，可按批准执行一次提交前恢复：

1. 查 `modeling describe --operation resume_pre_submit --schema` 和 `--operation pre_submit_recovery_statement --schema`。
2. 准备经调用方核实的恢复声明与一次性恢复授权，绑定原账当前 SHA256、原批准、原尝试、原请求和旧作业停止证据。若原账采用增量账本（`ledger_schema_version: "1.2"`），在恢复声明里显式填写 `"ledger_schema_version": "1.2"` 并附原派生批准与备份路径，只针对该账本里唯一新增的 `reserved` 行恢复。
3. 运行 `resume_pre_submit`：工具沿用原账路径、原尝试和原额度，把 `reserved` 一次性转为 `submitting` 并保存旧账备份，随后上传同图并最多发出一次原生成请求。
4. 受理后 `task_id` 先写入受理回执再更新原账。若写账过程出现宿主中断，查看报告里的 `pre_submit_recovery.accepted_task_id` 与回执文件，确认文件完整后转 `bind_unknown` 或 `query` 接着走。

## 限流与海外接口边界

- **HTTP 429 限流（退出码 `12`）**：CLI 返回 `RATE_LIMITED` 以及频率码（`1007`）、并发码（`2000`）和有效退避秒数（`0..86400`）。按提示等待后再发起新查询；遇到余额查询或上传阶段 429 保持原预约待命，遇到收费 POST 429 保留 `unknown` 先核实再决定。
- **海外接口（`tripo-overseas`）**：使用独立海外账本、批准和凭据范围，走 `query` 查询已知任务状态，用新 `download` 作业重新获取完整新鲜 GLB；遇到上传失败、未知提交或费用字段缺失时保留原预约和脱敏回执，核实清楚再处理。
- **安全诊断日志**：HTTP 诊断字段只保留状态码、数值业务码、连接类别和可选 `request_id` 摘要，把令牌和签名链接隔离在内存里，交接和汇报时直接引用脱敏报告路径。
