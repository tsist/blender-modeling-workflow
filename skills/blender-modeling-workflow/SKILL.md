---
name: blender-modeling-workflow
description: 把外部生成的三维候选导入为原生 .blend，做同条件比较、按部件分流沿用或重建，并整理成可交给 UV、材质和审核的建模工作副本；也涵盖中国站 H3.1 API 生成与中断作业恢复。接到角色、雕塑感主体或复杂道具的外部生成件，或要接手已有 GLB、FBX、Studio Bridge 候选与云端任务时用它。
metadata:
  version: "1.0.0"
---

# 外部生成候选导入与部件整合

本技能属于 [建模部门](https://github.com/tsist/blender-ai-workflow/blob/main/skills/blender-work-methodology/references/departments/modeling.md)，负责把外部生成的三维候选接进原生 Blender 工程，完成同条件比较、部件分流和白模交付，也在需要时经公共 CLI 调度中国站 H3.1 API 生成并恢复中断作业。

## 怎么做

### 第一步：摸清已有产物，选定入口

先看手里已经有什么文件，能复用的直接进本地交接，省下重复生成的时间和积分。

- **已有 Studio 导出或 Bridge 到件**：直接接本地导入与审查。需要网页端生成或导出新变体时，先走 [tripo-studio-opencli](https://github.com/tsist/tripo-studio-opencli/blob/main/skills/tripo-studio-opencli/SKILL.md)。
- **需要 API 生成或接手云端任务**：先查实机目录和合同，按批准范围提交或续查。
- **动手前先查当前命令**：用公共入口核对最新子命令与开关，省去猜参数名。

```powershell
$ctl = './blenderctl.ps1'
& $ctl discover --product modeling
& $ctl modeling --help
& $ctl --modeling-release 0.1.14 modeling --help
```

### 第二步：把外部候选导入为原生 `.blend`

每次任务在 `work/audit/<batch>//jobs` 建独立作业目录，原文件留着对照。按候选格式选对应入口：

- **Studio 静态 FBX（带同次导出清单）**：先跑 `& $ctl modeling describe --operation studio_fbx --schema` 核对字段，再用 `modeling run --params $paramsFile` 直入，完整保住原角点法线。
- **本地静态 GLB**：把 `--modeling-release 0.1.14` 放在子命令前面，先跑 `modeling describe --operation import_local --schema`，再提交 `modeling run --params $paramsFile` 完成导入、贴图打包、双项重开校验与预览出图。
- **通用静态 FBX**：用 `& $ctl --jobs-dir $jobs exchange import $fbx --expected-sha256 $sha --manifest $importManifest` 导入，再另起进程重开核对。
- **Studio Bridge 原生候选**：经 `discover --action bridge.handoff` 取得 `handoff.json` 与相邻 `candidate.blend`；跨宿主版本编辑时先用 `project prepare-copy` 升到当前工作副本。

具体请求写法和清单字段见 [外部候选导入、审查与采用](references/blender-handoff.md)。

### 第三步：审查候选并采用到目标工程

先把报告、导出清单和候选指纹核对无误，再把候选追加进目标工程的新副本。目标工程里的手调内容先存盘，再算 SHA256 传给采用命令。

```powershell
# 1. 复查报告与候选 SHA
& $ctl --jobs-dir $jobs modeling review-candidate $report --expected-report-sha256 $reportSha

# 2. 追加为目标工程新副本里的活动场景
& $ctl --jobs-dir $jobs modeling adopt-candidate $targetBlend --expected-sha256 $targetSha --report $report --expected-report-sha256 $reportSha
```

采用成功后，顺着返回里的 `data.output` 新工程路径、`data.scene` 场景名和 `data.bridge_id_map` 重命名对照表往下做。带 Studio 磁盘导出清单的旧报告，同步传入 `--studio-export-manifest` 和 `--expected-studio-export-sha256`。

### 第四步：同条件比较候选，做几何体检

对比多个候选时，锁定同一套机位、构图、灯光、渲染引擎和色彩管理。按正背、四分之三和脸手接合局部对照参考单，画面优劣一眼分清。

- **几何与贴图分开看**：先看白模轮廓、比例和遮挡，再看带贴图画面。贴图容易遮住发软的轮廓和粘连结构。
- **跑一次只读几何体检**：核对单位、尺寸、轴向（常见生成件正面朝 `+X`、高度约 `1.0 m`；整理交付白模时统一旋正为正面朝 `-Y`、底面落零并应用缩放，对齐商业审核 G2）、UV 层、材质槽和打包贴图状态。

```powershell
& $ctl --jobs-dir $jobs --compact model inspect $candidate --expected-sha256 $candidateSha --qa-profile game
```

### 第五步：按部件分流——沿用生成网格还是重建控制笼

把模型拆成部件逐一判断，同一件作品里不同部件灵活混用路线，把工夫花在主画面和关键结构上：

- **直接沿用生成网格**：轮廓准确、雕花扎实且用于静态展示或贴图细化的有机主体与复杂配饰，留住原件并另存制作副本。
- **局部整理与重拓扑**：面数过密或有布尔碎面的部件，优先用可编辑修改器调整，或在制作副本上经 `model prepare` 做平面融并（`DECIMATE` `DISSOLVE`）和重拓扑回投影（`QUADRIFLOW_SHRINKWRAP_V1`）。重拓扑后会重建拓扑，下一步记得重展或迁移 UV。
- **转交硬表面重建稀疏控制笼**：平直壳体、规则倒角、精确开孔、装配转轴和需要反复改型的扣件，把生成候选摆作空间参考，转交 [blender-hard-surface-modeling](https://github.com/tsist/blender-hard-surface-workflow/blob/main/skills/blender-hard-surface-modeling/SKILL.md) 从稀疏四边面控制笼重做。

需要通过中国站 H3.1 API 补充生成或做分件补全时，先出无贴图白模，白模达标再追加贴图；具体做法见 [中国站 H3.1 生成、选型与有界批量](references/api-generation.md) 与 [部件分流、局修与材质 UV 交接](references/parts-and-handoff.md)。

### 第六步：中断作业按状态接着走

遇到超时、限流、下载断片或提交状态不明时，先查已有本地作业和原账，手里已有有效产物就直接接手。

```powershell
# 沿用原 --jobs-dir 与 job_id 查状态、等终态或取结果
& $ctl --modeling-release 0.1.14 --jobs-dir $origJobsDir job status $jobId
& $ctl --modeling-release 0.1.14 --jobs-dir $origJobsDir job wait $jobId --wait-seconds 30
& $ctl --modeling-release 0.1.14 --jobs-dir $origJobsDir job result $jobId
```

已知远端 `task_id` 走 `query` 或 `resume_download`；无 `task_id` 先凭控制台或受理回执核实对应关系，再走 `bind_unknown` 或 `resume_pre_submit`。完整分支见 [中断作业恢复、断点续传与限流处理](references/recovery.md)。

## 交给下游

- **给 [建模审核](https://github.com/tsist/blender-modeling-review/blob/main/SKILL.md) 与用户白模审看**：交付另起进程冷重开通过的 `.blend` 工作副本（路径与 SHA256）、候选来源索引、部件分流表（写清哪些沿用、哪些重拓扑、哪些转硬表面重建）、同条件白模对照图和 `model inspect` 体检报告。
- **给 [UV 部门](https://github.com/tsist/blender-uv-workflow/blob/main/skills/blender-uv/SKILL.md) 与 [材质部门](https://github.com/tsist/blender-material-workflow/blob/main/skills/blender-material-authoring/SKILL.md)**：提供真实对象名、材质槽、UV 层与打包图像清单，标出 `Base Color`（`sRGB`）、法线/粗糙度/金属度（`Non-Color`）及 ORM 通道实际连接，列出缺 UV 或待重贴的部件。
- **交付前另起进程验真图**：每次搬动目录或改完副本，对最终路径重跑 `inspect` 并打开渲染图复核，确认资源闭合且画面无误再交接。

## 例子：接手雕花提灯候选并分流交接

接到委托：前期参考部门交来古风雕花提灯的正背参考图，Tripo Studio 已导出静态 GLB 和下载清单，要求接进 `work/lantern/base.blend`，整理成可审白模并准备交 UV 与材质。

1. 建批次目录 `work/audit/<batch>/`，算出 GLB、下载清单和 `base.blend` 的 SHA256。
2. 用 `--modeling-release 0.1.14` 跑 `modeling describe --operation import_local --schema` 核对参数，提交 `import_local` 生成原生 `.blend` 候选与正背、四分之三白模预览，确认同进程与新进程重开都通过。
3. 跑 `modeling review-candidate` 复查导入报告与下载清单指纹，再跑 `modeling adopt-candidate` 把提灯追加到 `base.blend` 新副本的活动场景。
4. 对新副本跑 `model inspect --qa-profile game`，同时对照参考单看白模三视图和灯盖近景。灯身藤蔓雕花大形扎实，划入直接沿用并轻度减面；灯顶圆环和底座转轴边缘发软，划入硬表面重建。
5. 保留雕花灯身，把灯顶圆环与底座转轴交 [blender-hard-surface-modeling](https://github.com/tsist/blender-hard-surface-workflow/blob/main/skills/blender-hard-surface-modeling/SKILL.md) 对着候选轮廓建稀疏四边面控制笼，拼回同一工作副本后另起进程冷重开复检。
6. 把最终白模副本、SHA256、部件分流表、贴图通道清单和同条件白模对照图交给 [blender-modeling-review](https://github.com/tsist/blender-modeling-review/blob/main/SKILL.md) 审看，通过后请用户确认白模，再转交 [blender-uv](https://github.com/tsist/blender-uv-workflow/blob/main/skills/blender-uv/SKILL.md) 和 [blender-material-authoring](https://github.com/tsist/blender-material-workflow/blob/main/skills/blender-material-authoring/SKILL.md)。

## 参考

- [外部候选导入、审查与采用](references/blender-handoff.md)
- [部件分流、局修与材质 UV 交接](references/parts-and-handoff.md)
- [中国站 H3.1 生成、选型与有界批量](references/api-generation.md)
- [中断作业恢复、断点续传与限流处理](references/recovery.md)
- [合同、Schema 与证据路径索引](references/sources.md)

协作技能：[tripo-studio-opencli](https://github.com/tsist/tripo-studio-opencli/blob/main/skills/tripo-studio-opencli/SKILL.md) · [blender-modeling-reference](https://github.com/tsist/blender-modeling-reference/blob/main/SKILL.md) · [blender-hard-surface-modeling](https://github.com/tsist/blender-hard-surface-workflow/blob/main/skills/blender-hard-surface-modeling/SKILL.md) · [blender-modeling-review](https://github.com/tsist/blender-modeling-review/blob/main/SKILL.md) · [blender-uv](https://github.com/tsist/blender-uv-workflow/blob/main/skills/blender-uv/SKILL.md) · [blender-material-authoring](https://github.com/tsist/blender-material-workflow/blob/main/skills/blender-material-authoring/SKILL.md) · [blender-cli](https://github.com/tsist/blenderctl/blob/main/skills/blender-cli/SKILL.md)
