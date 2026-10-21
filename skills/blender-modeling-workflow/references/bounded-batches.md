# 显式有界批量

需求包含多个明确请求或已有 task 时才使用。以下批量合同仅适用显式 `tripo-cn` 和匹配账本；海外与 Meshy 不开放批量。批完成不等于完整角色或动画质量通过，逐项保留质量和选用状态。配套 CLI 发行版为 0.1.13，调用前读实际 describe/Schema；本次发行未新增真实付费批量或建模验证。

## 已批准请求

发现 `modeling describe --operation submit_batch --schema`，经 modeling.run 构造 manifest：1–8 个唯一 operation_id 的规范 requests、原唯一 ledger_file、credential_ref、authorization_descriptor、query_limit（整 job task GET 总尝试 1–120）、poll_interval_seconds（1–30）。每项模型、输入路径/SHA 和固定父 task 须已在具体批准内；approved:true 或批准文件存在不证明真实授权。预算/外发范围变化按[提供商与预算](provider-and-budget.md)处理，不复制空账或换 ID。

原生 worker 预验整批批准/PNG/来源和最坏额度、次数，在原账锁下串行创建。前项 succeeded、有明确消费（0 合法）与 receipt 且账未 halted 才继续；成功缺消费保持预约并有限查询。每次 task GET 前计数，余额 GET 不算此数但另记录；查询额度不足时不创建无法查询的新项。根据批规模显式设置公共 timeout，不假定默认 120 秒够用。

原生 job 按显式间隔等待并查取消；模型不循环轮询。HTTP/429、远端 failed/cancelled/expired、缺结算耗尽 GET 上限、未知 POST、取消/超时停止全批。不重试 POST、退款、充值或切厂商。先留 accepted 回执，再逐阶段记录 modeling-batch.json 的已完成项、remaining_ids、active_id/task_id、query_count、stage。

确认旧 job 不再运行后，显式新 job 用原请求/批准/账核对 operation_id、request SHA、task 映射：成功已结算跳过，accepted 只 GET；reserved/submitting/unknown 或失败先按[恢复](recovery.md)处理。新 job 有独立显式 GET 上限，不自动续跑；journal 不是授权或调度器。声明的 PNG/批准仍须存在并受守护；accepted 参考丢失可先用 known task 独立 query，再只提交输入完整的未处理项。

父 source_task_id 调用前必须真实存在于同账。本入口不动态绑定生成→分件→补全，不包含 Blender 导入/审美选用，不进入可重放 DAG。同账后处理批准接续见[预算指南](provider-and-budget.md#同账追加后处理批准)。

## 已有任务下载

发现 `download_batch` Schema。items 为 1–16 个唯一 task，每项显式 download 或 resume_download 及 max_bytes；恢复携原 recovery_descriptor/partial_descriptor。max_total_bytes 覆盖各 max_bytes 之和，界定完整产物而非网络流量。复用原 ledger/credential，不新增生成批准，也不保证远端仍可下载。

先验证全批恢复源，再串行写新 job 的索引目录。首失败停止，成功项保留，新 job 只放剩余/失败项。partial/checkpoint 用真实 SHA，原件只读；当前 URL、资源身份、强 ETag、Range 或长度不符则停止，不自动全量重下。final 已晋升而 job 被取消时先独立核验现有文件。

回读 modeling-batch.json 的 completed/current_item/failure_recovery；compact 给 batch_report 与失败项 partial/checkpoint/output 的路径/SHA。引用或 validated:false 不证明恢复成功，原 worker 停止后再核对源 SHA。[下载访问失败](recovery.md#限流与下载访问失败)区分不可用和明确 expired。

当前批量守卫、受控故障与恢复有本地证据；真实付费批量、云幂等/取消退款、真实 CDN、断电、任意非合作 writer 未由合成测试证明。最终逐项按[交付](delivery-and-review.md)验收。
