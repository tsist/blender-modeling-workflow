# 中国站 H3.1 生成、选型与有界批量

当需要通过公共 CLI 调用云端接口生成三维候选或批量下载已有任务时，先核对实机合同与预算边界，再按两阶段几何优先节奏提交。日常有 Tripo Studio 会员时，优先走 [tripo-studio-opencli](https://github.com/tsist/tripo-studio-opencli/blob/main/skills/tripo-studio-opencli/SKILL.md) 网页流程，再把导出文件交回本地导入；需要独立 API 钱包或批量脚本时再走本指南。

## 第一步：查实机厂商目录与 Schema

每次提交前先跑只读查询，以实机返回的可执行操作和 Schema 为准，照着合同拼 UTF-8 JSON：

```powershell
$ctl = './blenderctl.ps1'

# 查看中国站可用操作与参数约束
& $ctl --modeling-release 0.1.14 modeling describe --provider tripo-cn

# 查看单个厂商操作合同或公共 manifest 合同（二者分开查询）
& $ctl --modeling-release 0.1.14 modeling describe --provider tripo-cn --provider-operation h31_multiview --schema
& $ctl --modeling-release 0.1.14 modeling describe --operation submit --schema
```

把 `modeling describe` 返回的 `implementation_sha256` 填进请求参数，凭据用环境变量安全引用（`credential_ref`），让密钥留在环境里，不写进日志和交接单。

## 第二步：准备参考输入

参考图直接决定生成轮廓上限。需要从零做风格图、主效果图、正交视图和尺寸表时，先交给 [blender-modeling-reference](https://github.com/tsist/blender-modeling-reference/blob/main/SKILL.md) 出图包，再按下面规则挑图提交：

- **优先正背双视图**：中国站 `h31_image`（单正面）与 `h31_multiview`（有序 `[front, back]`）基础预留同为 `20` 积分。只要背面设计明确，优先补齐背面走 `h31_multiview`，能大幅减少背面结构错位。
- **分图保存与清洁构图**：每张提交图独立存成无损 PNG（建议 `20 MB` 以内），保留原图、修订图、视图标签（`front`/`back`）和 SHA256。画面无文字水印、边缘清晰、光照平顺少硬投影，方便后续去光照贴图。角色尽量用 A 姿或 T 姿张开手脚，减少四肢与衣摆粘连。
- **拼图留作审看**：多视图拼图给用户审看用，API 调用时按具名字段传入单张原图。参考图本身有身份矛盾或缺关键角度时，先修好参考再提交，省去无效试错。

## 第三步：两阶段几何优先与模型选型

云端生成坚持先定白模再做精贴，避免轮廓没过关却多花贴图积分：

1. **阶段一（白模定型）**：`h31_image`、`h31_multiview`、`h31_parts_multiview` 和 `p2_multiview` 底层固定发 `texture=false, pbr=false`。生成下载后先导进 Blender 摆同机位看大形、比例和遮挡。
2. **阶段二（达标后精贴）**：白模过关且获得同账批准追加后，再对同一账本内已成功结算的原生成父任务调用 `texture_v35`（`detailed`、`pbr=true`、`bake=true`、`delight=true`），自动去掉原参考里的硬阴影。

### 中国站（`tripo-cn`）常用操作与成本口径

API 积分与网页 Studio 会员积分分账独立，预算审批时按 `1 积分 ≈ ¥0.07` 换算估算成本，实际扣费以云端终态回执的 `credits_consumed` 为准。

| 操作 | 适用场景 | 常见基础预留 | 关键衔接要求 |
|---|---|---|---|
| `h31_multiview` | 日常主力，角色与雕塑感主体白模首选 | 20 积分（约 ¥1.40） | 有序 `[front, back]` PNG，无贴图白模 |
| `h31_image` | 只有单正面参考时快速起型 | 20 积分（约 ¥1.40） | 单张 `front` PNG，无贴图白模 |
| `h31_parts_multiview` | 探索起点带原生分件的多部件主体 | 最低 40 积分 | `front` + `back` PNG；输出后逐壳查粘连与内部缺口 |
| `p2_multiview` | 明确需要 P2 构型对比时按需选用 | 100 积分（约 ¥7.00） | 当前适配器输出静态 GLB，未开放 `face_limit`/`quad`/FBX |
| `segment_v2` | 对已过关白模做云端语义分件 | 40 积分 | 传入同账已成功结算的原生成父 `task_id` |
| `texture_v35` | 白模达标后生成 PBR 与去光照贴图 | 20 积分 | 传入同账已成功结算的原生成或 `h31_parts_multiview` 父 `task_id` |
| `complete_all_v1` | 对分件结果做全件封闭补全 | 50 积分 | 传入同账已成功结算的 `segment_v2` 父 `task_id`，整件补全省略 `part_names` |

实机合同未开放的选项（如 `face_limit`、`quad`、`smart_low_poly`、`model_seed`、云端减面与格式转换），保持空缺不填入 manifest；需要减面、重拓扑、展 UV 或四边精修时，交回 Blender 本地工序。

### 海外接口与只读厂商查询

- **海外基础接口（`tripo-overseas`）**：在 `manifest` 1.1 里显式声明 `provider: "tripo-overseas"` 与独立海外凭据、批准和账本，支持 `account_balance`、`submit`（`h31_image`/`h31_multiview` 标准无贴图几何）、`query` 和完整新鲜 GLB `download`（显式设 `max_download_bytes`）。海外与中国站账本、任务和凭据各自独立。
- **Meshy（`meshy`）**：提供已有任务的只读单次状态查询（显式指定 `remote_operation`），用于跟进外部已有任务状态。

## 第四步：守住原账边界与同账追加批准

把每一次收费提交绑在唯一的 `ledger_file`、规范请求 SHA256 和批准上限上：

- **预算覆盖内直接推进**：在已批准的费用、参考图、外发范围和轮数内，自主完成生成、下载、导入、对比与局部整理；新增收费操作、新厂商或超出原上限时，先写清楚具体方案和预算增量请用户拍板。
- **已有父任务再追加后处理**：白模生成拿到真实父 `task_id` 并结算后，用纯本地操作 `approval_append_prepare` 生成审阅计划和 SHA256，再用 `approval_append_apply` 在原账本上追加精确的后处理请求（如 `texture_v35`、`segment_v2` 或 `complete_all_v1`）。原账所有历史尝试须处于终态且无未结 `unknown`，追加操作保住原有序前缀与历史费用。
- **保护财务记录完整性**：保留原账路径、已有尝试记录与预约状态；遇到状态不明或提交超时，立刻转 [中断作业恢复、断点续传与限流处理](recovery.md) 核查，留住原记录接着走。

## 第五步：显式有界批量（`submit_batch` / `download_batch`）

有多项已批准请求或多项已有任务要集中处理时，走显式有界批量入口：

- **批量提交（`submit_batch`）**：先查 `modeling describe --operation submit_batch --schema`。单批放 `1–8` 个唯一 `operation_id` 的已批准请求，绑定同一 `ledger_file`、`credential_ref` 和 `authorization_descriptor`，并显式填好整批 `query_limit`（`1–120`）、`poll_interval_seconds`（`1–30`）和足够长的外层作业超时。系统按顺序逐项推进，前一项结算成功再开下一项；遇错、限流或超时立即停批，保留 `modeling-batch.json` 里的 `completed` 和 `remaining_ids`。
- **批量下载（`download_batch`）**：先查 `modeling describe --operation download_batch --schema`。单批放 `1–16` 个唯一已知任务，每项写明 `download` 或 `resume_download` 与 `max_bytes`，外层设 `max_total_bytes` 覆盖全部产物上限。首项失败即停批并保留已下载成品，重跑时只放剩余任务。
- **逐项验收**：批量跑完后，把每个下载好的候选送进 [外部候选导入、审查与采用](blender-handoff.md) 单独导入、冷重开并同条件比图，选出最佳候选再决定是否继续精修。
