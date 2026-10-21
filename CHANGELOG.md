# Changelog

## 0.1.13 — 独立仓库发行

- 新增显式 `tripo-overseas` 基础适配器：manifest 1.1 的 submit/query/account_balance/download。
- 海外生成限定 H3.1 单 front 或 front/back 双 PNG、standard 几何；贴图、PBR、分件、quad 和 smart-low-poly 固定关闭。
- 海外凭据 scope、批准、账本及请求 SHA 域独立；金额最多两位小数，费用缺失保留预约；下载显式限制字节数。
- 海外仅有基础模拟测试及一次真实余额 GET，未执行模型生成、真实扣费或 Blender 验收。未新增海外自动重试、跨区 fallback、batch、resume、migration 或 bind。
- 将插件、建模专用 CLI 和单主技能整合为独立源码发行。`scripts/modeling_workflow.py`、PowerShell wrapper及解包后的发现入口已通过基础检查；无需原项目目录或另一份公共CLI。
- 保留文件锁、进程树、输入指纹和作业终态保护；排队请求漂移会落为明确失败，已完成作业拒绝重放。新增可重复构建、源码ZIP与无账户基础CI。

## 0.1.12 及以前 — 既有能力概要

- 中国站固定模型操作、本地 GLB 候选报告、后台保存和显式面板采用。
- 按账本版本和证据合同提供受限批准追加、增量及一次性提交前恢复；保留旧财务状态，不自动重发未知提交。
- 原图采用官方 presign 与独立 PUT；有限下载检查点及批量动作保留各自前置条件。
- Meshy 仅实现已有任务的只读 query，未实现收费提交、下载或费用账。

历史真实案例、模拟适配器测试和本次公开整合分别说明；本清单不表示所有操作都完成真实收费或跨平台验收。
