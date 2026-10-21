# 恢复已有任务

先读取原 request/job/report、候选/SHA、task_id、原账与下载状态，判断停在提交前、未知提交、远端运行、下载或本地制作。恢复不等于新增收费；accepted task 不再 POST。仅取消本任务拥有的本地作业，本地取消不证明云取消或退款。

| 证据 | 下一动作 | 停止条件 |
| --- | --- | --- |
| 有效本地 GLB/贴图 | 核对 SHA 后[本地交接](blender-handoff.md) | 损坏或来源不明时不重新收费 |
| `.blend` 和报告 | 重开并继续受影响制作 | 文件变化重新取证，不覆盖手改 |
| 已知 task_id | 同 task query，再按状态 download | 失败保留同 task，不重 submit |
| 未知 submit | 核查原账/受理证据，仅支持的中国站合同可 bind | 无证据保持 unknown 与预约 |
| partial/checkpoint | 确认原 worker 停止后按支持合同恢复 | 身份、SHA、长度或 validator 不符就停止 |
| 本地 job 运行 | 同 jobs-dir status/result 或一次有界 wait | queued/running 不认定成功，不循环轮询 |

使用已定位的 `$modelingCli` 和原作业目录：

```powershell
& $modelingCli --jobs-dir $modelingOriginalJobsDir job status $modelingJobId
& $modelingCli --jobs-dir $modelingOriginalJobsDir job result $modelingJobId
& $modelingCli --jobs-dir $modelingOriginalJobsDir job wait $modelingJobId --wait-seconds 30
```

本地 job 与远程 task 分别判终态；CLI job_id 不传远程 API。query 可能写观察/结算记录，不把带账 query 一概视作无副作用。

## 海外恢复边界

海外仅 manifest1.1/tripo-overseas 的 submit/query/account_balance/download。独立 global 凭据 scope、账、批准和请求 SHA 域不接受中国站或研究账。上传失败、未知 POST 或终态费用缺失保留预约/次数，不清账、不推定零费用或自动重试。已知 task 可显式单次 query；未知提交本版无 bind/resume/migration 路径。

download 内取同 task 当前 URL，只作新鲜完整 GLB，显式给 max_download_bytes；不续接中国站 checkpoint，无自动 retry、跨区 fallback 或 batch。20 credits为公开估价，非硬扣费保证。本次发行只做基础模拟及一次真实余额 GET，无海外模型生成、真实扣费或新增 Blender/建模验收。完整范围见[提供商与预算](provider-and-budget.md#海外基础接口)。

## 原图上传

Tripo 使用同地区官方 files/presign 小 JSON 申请，再以独立无 Bearer HTTPS PUT 发送原 PNG。固定16KiB块、30秒空闲及180秒单图总时限；完整发送并获存储HTTP200才返回token。记录输入 SHA、完整字节数及安全回执，本地写入量不证明服务端收件量。签名 URL/token 不落日志，不降图或自动回退重传。

外层公共 job timeout 覆盖全部上传及后续API，两图上传上限合计360秒，另留API时间。上传成功不证明生成受理、费用结算或模型质量，也不能释放旧预约。

## 中国站提交前单次恢复

仅 manifest1.1、显式tripo-cn，先发现 resume_pre_submit 和 pre_submit_recovery_statement Schema。适用于未认领 reserved、无task_id 的原预约；必须有对应的一次恢复授权，并有据核实原job/owner停止、generation POST从未开始。reserved、tasknull、耗时或缺日志均不足以证明未POST。证据不足或已有submitting/unknown/confirmed时停止该路径。

声明绑定原账当前SHA、原approval/attempt/operation/request、图像SHA、旧实现身份及核实证据。普通ledger1.0用声明schema1.0；ledger1.2必须声明schema1.1及 ledger_schema_version:"1.2"，验证完整backup/增量授权/request/派生approval四份根证据和全部旧有序前缀，只允许唯一新增未认领reserved行。原派生approval保持原路径/SHA，全部旧冻结记录不能套普通写账query或bind。

沿用同路径/attempt/预约/次数，只认领一次，不新reserve；原请求、批准和证据持有到云请求结束，mutable ledger由模块CAS。认领前本地核查/余额GET不创建attempt；认领后上传失败、取消或POST未知均保unknown/保守submitting及原预约，禁止第二次恢复和财务回滚。受理task先写acceptance回执再确认原账，失败时核对 pre_submit_recovery.accepted_task_id、acceptance_receipt_persisted 和真实文件/SHA；布尔值不能单独证明持久文件或授权绑定。

## 中国站有据绑定未知提交

发现 bind_unknown/binding_statement Schema后，使用原账、原规范request与binding_descriptor，不新attempt或上传。声明绑定原批准/当前账SHA、attempt/operation/request身份和经核实task；验证来源须为控制台或找回受理回执及对应本地证据，不按类型、时间、消费猜映射。

仅一次GET核对task/操作，然后按账SHA更新映射，预约/次数保持。accounting_deferred/followup_query_required表示仍需后续query结算；缺费用不释放。此合同不自动找回未知POST，也不承诺真实云幂等、取消或退款。

## 中国站下载检查点

确认原worker终态或有据owner退出，保留原partial/checkpoint。resume_download使用同task/credential/原账及真实recovery_descriptor/partial_descriptor路径和SHA，新job写新目录。仅复制checkpoint确认前缀，崩溃多出的尾部留原件；完整未晋升checkpoint可只做本地GLB检查。

续传必须同HTTPS origin/path摘要、强ETag摘要、准确206/Content-Range及长度。无原生checkpoint、缺validator/长度、忽略Range、资源变更或坏SHA均拒绝，不猜拼接或自动全量重下。完整GLB校验、SHA和无覆盖晋升后才成功；签名URL仅内存，checkpoint只保摘要。单次socket阻塞可能延迟取消，不承诺即时中止、断电恢复或真实CDN续传已验。

## 限流与下载访问失败

HTTP429给安全类别与有限延迟提示，不是重试许可；适配器不自动睡眠重试。收费POST429仍可能unknown，上传429保原预约，余额GET429在预约前停止。不换账或ID。中国站 query_batch每项单次GET，错误/限流/取消/超时停止；远端running/failed是快照，不等于整批生成通过。批量范围见[有界批量](bounded-batches.md)。

模型HTTP401/403/404/410表示当前artifact不可用，不单凭状态认定删除或过期。旧job停止后，无残片可显式新download；有效双文件才按支持合同resume。内部重取同task URL，不额外循环query刷新。明确expired就停止刷新、查本地文件或核实保留政策，不据此释放预算。DNS/TLS/API403和下载失败分开诊断。

安全HTTP诊断只保状态、白名单数值业务码、固定连接类别及可选request_id摘要，不输出远端message/body/token/URL。反复失败缩小诊断或报告缺口；批准CAS异常按[同账批准恢复定位](provider-and-budget.md#同账追加后处理批准)向前核对，绝不恢复旧财务快照。
