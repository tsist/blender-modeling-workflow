# 能力与限制

以下说明 0.1.13 的适配器范围。当前 `modeling describe`、动作 Schema 和精确实现身份决定可执行字段；厂商文档中的全部功能不等于本项目已实现或已验。

## 本地 Blender 交接

支持静态、未绑定、资源嵌入的 GLB，导入上限 256 MiB；后台产出持久 `.blend`、候选报告及预览。插件用于读取报告并显式采用到新场景，不自动保存。已有案例验证过保存、独立重开及有限 UI 操作，不能外推任意共享数据、绑定场景或全部场景属性保护。

包含不支持的 required glTF extensions、骨骼或动画的输入不在当前导入合同内。绑定、动画、通用自动重拓扑、合身和完整角色还原不属于已证明能力。

## 提供商

| 提供商 | 执行动作 | 证据与限制 |
| --- | --- | --- |
| `tripo-cn` | H3.1 单图/正背双图、原生分件双图、P2 双图；固定语义分件、贴图和全件补全；受限查询、下载与恢复 | H3.1 基础和原生分件各有有限真实收费案例；其他公共适配器模拟测试、历史服务调用和真实收费执行不能互相替代。语义层次与模型质量仍有失败案例 |
| `tripo-overseas` | `submit`、`query`、`account_balance`、`download` | 基础模拟测试及一次真实余额查询；未做真实模型生成、扣费或 Blender 验收 |
| `meshy` | 已有 image-to-3d、multi-image-to-3d、text-to-3d、retexture、remesh 任务的单次 `query` | 只读适配器模拟测试；真实账号与质量未验；无提交、下载、费用账、批量或恢复 |

### 海外基础合同

仅 manifest 1.1、显式 `provider: "tripo-overseas"` 与 `region: "global"` 凭据 scope。`h31_image` 输入 front PNG，`h31_multiview` 输入有序 front/back 双 PNG。模型固定 H3.1 `v3.1-20260211`，wire 固定为：

```json
{"geometry_quality":"standard","texture":false,"pbr":false,"generate_parts":false,"quad":false,"smart_low_poly":false}
```

海外账本、批准与请求 SHA 域独立；不接受中国站或研究账迁移。下载仅对已有任务作新鲜完整 GLB 传输，显式给出 `max_download_bytes`。不开放自动重试、跨区 fallback、batch、resume、migration、bind 或其他模型/贴图/分件选项。

### 中国站恢复

按具体账本版本与匹配声明提供有限批量、已知任务查询、下载检查点、证据绑定及一次性提交前恢复。每种动作有独立前置条件，不是通用重放、自动重试或任意旧账迁移。既有受理任务不再次 POST；未知状态或缺少证据时保留预约。中国站恢复能力不自动应用于海外或 Meshy。

## 验证边界

已有实际环境证据集中于 Windows / Blender 5.2.1。声明的 Blender 最低版本不构成所有版本或跨平台通过的保证；0.1.13 未新增海外模型生成、Blender 安装/启用或视觉验收证据。模拟传输不能证明真实 CDN 的 Range/ETag、签名续期、断电恢复、退款或云端幂等。原始模型、实际预览、保存与重开结果仍需按当次目标验收。
