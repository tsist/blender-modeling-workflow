---
name: blender-modeling-workflow
description: 组织外部模型生成与 Blender 建模交接，从参考多视图、候选比较到分件、材质、保存交付和已有任务恢复。适用于角色或复杂主体的生成制作，也可从已有参考或本地模型进入；绑定与动画交给外部专项能力。
---

# Blender 建模工作流

以可编辑模型、原始产物、实际预览和明确制作缺口为交付。已有参考、GLB、`.blend` 或远程任务从对应阶段进入，不重新生成有效产物。预算、素材外发范围和轮数已有授权时在边界内继续；新厂商、新费用或新增素材外发先形成具体方案并补齐未覆盖的授权。后台候选不自动替换用户当前场景。

| 当前需要 | 按需指南 |
| --- | --- |
| 设定、参考、多视图 | [参考准备](references/brief-and-reference.md) |
| API 能力、地区、权利与预算 | [提供商与预算](references/provider-and-budget.md) |
| 生成、比较、有限迭代 | [生成与比较](references/generate-and-compare.md) |
| 多个已批准请求或已有任务下载 | [有界批量](references/bounded-batches.md) |
| 分件、遮挡、局部补全与适配 | [分件与补全](references/parts-and-completion.md) |
| 本地 GLB 导入、候选报告与采用 | [Blender 交接](references/blender-handoff.md) |
| UV、贴图与材质精修 | [材质协作](references/material-cooperation.md) |
| 预览、保存与交付 | [交付与审阅](references/delivery-and-review.md) |
| 中断、未知提交、限流或下载失败 | [恢复](references/recovery.md) |

## 执行环境

配套 CLI 发行版为 0.1.13，要求已有 Python 3.12+。将 `BLENDERCTL_ROOT` 指向本仓库根，直接入口为 `tools/blenderctl/cli.py`；根 `blenderctl.ps1` 支持 `BLENDERCTL_PYTHON` 指定 Python。Python、Blender、输入和作业目录使用用户环境中已确认的绝对路径，不假定盘符、用户名或捆绑运行时。全局 `--blender`、独立 `--jobs-dir` 放在子命令前；本发行没有通用事务入口，不依赖 BLENDER_PATH。先核对当前 `modeling describe`、帮助、Schema 和 `implementation_sha256`，版本号本身不能证明合同匹配。

本地交接目标为 Windows / Blender 5.2.x，现有实际案例集中于 5.2.1；不据此保证其他版本或跨平台通过。本技能可以独立分发，但不包含 CLI、Blender、账号或图像/材质工具。专项技能缺失时说明所需外部能力，使用已存在且可核对的工具；不伪造入口或自动安装。绑定与动画需要额外专项工具和验收。

每个候选保留文件/SHA、来源、操作与父候选关系，原件与制作副本分开。远程 task_id、本地 job_id、候选身份和文件 SHA 不互相替代。报告用于导航，实际模型、解码图像和独立重开决定验收。

## 公开能力边界

中国站 H3.1 基础生成和原生分件有有限真实收费 submit→query→download→导入/重开案例；其他公共适配器模拟测试与历史服务调用分别记录，不能拼成全操作真实收费验收。语义分件、隐藏表面、身份还原和 UV 质量须按本次目标检查。

海外仅基础 submit/query/account_balance/download，固定 H3.1 单 front 或 front/back 双 PNG、标准无贴图几何；凭据 scope、账、批准与请求 SHA 独立。本次发行仅基础模拟测试及一次真实余额 GET，未进行海外模型生成、真实扣费或新增 Blender/建模验收。Meshy 仅已有任务单次 query，未实现生成、下载或费用账。详见[能力证据概要](references/provider-and-budget.md#能力证据概要)。

本地整理、材质、GUI 采用、真实云生成分别验收；技术通过不代表艺术选用、完整角色、绑定或动画通过。软件和配套技能为 GPL-3.0-or-later，见 LICENSE；软件许可不授予参考或生成资产权利。
