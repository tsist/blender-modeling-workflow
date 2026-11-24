# 合同、Schema 与证据路径索引

本文件集中列出建模候选导入、中国站 H3.1 生成、采用与恢复相关的 CLI 发现入口、项目文档、外部接口资料和审计证据位置，需要核对底层字段或回查实证时按路径直读。

## 实时命令与 Schema 查询入口

```powershell
$ctl = './blenderctl.ps1'

# 浏览动作与版本
& $ctl discover --product modeling
& $ctl discover --product modeling --actions

# 查看工作区公共入口与 0.1.14 合同
& $ctl modeling describe
& $ctl modeling describe --operation studio_fbx --schema
& $ctl --modeling-release 0.1.14 modeling describe
& $ctl --modeling-release 0.1.14 modeling describe --provider tripo-cn
& $ctl --modeling-release 0.1.14 modeling describe --operation import_local --schema
```

独立发行脚本位置：`<PROJECT_ROOT>/runtime/modeling-workflow/0.1.14/scripts/modeling_workflow.py`。

## 项目契约与协作文档

- **部门工序卡**：[建模部门](https://github.com/tsist/blender-ai-workflow/blob/main/skills/blender-work-methodology/references/departments/modeling.md)
- **扩散建模与 CLI 合同**：`<PROJECT_ROOT>/docs/cli/扩散建模工作流.md` · `<PROJECT_ROOT>/docs/cli/阶段8建模工作流.md` · `<PROJECT_ROOT>/docs/cli/阶段13格式交换工作流.md`
- **采用与 UV/材质交接示例**：`<PROJECT_ROOT>/docs/cli/workflows/uv-material-handoffs.md`
- **Studio Bridge 交接**：[Studio Bridge 交接](https://github.com/tsist/tripo-studio-opencli/blob/main/skills/tripo-studio-opencli/references/studio-blender-bridge.md)
- **自研动作覆盖图**：`work/audit/<batch>/`

## 官方接口文档

- **Tripo 图生模型标准参数**：`https://developers.tripo3d.com/en/docs/generation-image-to-model/standard`
- **Tripo 多视图生成**：`https://developers.tripo3d.com/en/docs/generation-multiview-to-model`
- **Tripo 预签名文件上传**：`https://developers.tripo3d.com/en/docs/files-presign` · `https://developers.tripo3d.ai/en/docs/files-presign`
- **Tripo 官方定价页**：`https://developers.tripo3d.ai/en/pricing`

## 审计与实证目录索引

- **Studio 静态 FBX 直入、角点法线与面板冷开**：
  - `work/audit/<batch>/`
  - `work/audit/<batch>/`
  - `work/audit/<batch>/`
- **通用静态 FBX 交换清单样例**：
  - `work/audit/<batch>/`
- **Studio Bridge 原生候选接手与工作副本升级**：
  - `work/audit/<batch>/`
- **本地静态 GLB 导入、Studio GLB 采纳与冷开**：
  - `work/audit/<batch>/`
  - `work/audit/<batch>/`
  - `work/audit/<batch>/`
  - `work/audit/<batch>/`
- **中国站 H3.1 单项、同账批量与断点续传校核**：
  - `work/audit/<batch>/`
  - `work/audit/<batch>/`
- **H3.1 与 P2 同输入 A/B 实测对照**：
  - `work/audit/<batch>/`
