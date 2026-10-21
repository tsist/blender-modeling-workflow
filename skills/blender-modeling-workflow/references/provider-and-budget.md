# 提供商与预算

## 操作发现与凭据

先用 `modeling describe --provider <id>` 核对模型、输入输出、适配状态和证据。`--provider-operation <厂商操作> --schema` 读厂商合同，`--operation <manifest动作> --schema` 读公共动作，二者不同时使用。documented_features 不等于可执行参数，不以 latest 替换固定模型。

manifest 1.1 显式指定 provider；旧 1.0 保持固定中国站语义及原请求 SHA。中国账号只用于 `tripo-cn`，不探测海外或试跨区凭据。凭据使用环境变量或 DPAPI 引用，不把密钥值、签名 URL、token、Authorization 或原响应体写入报告。

中国站支持 `h31_image`、`h31_multiview`、`p2_multiview`、`h31_parts_multiview`、`segment_v2`、`texture_v35`、`complete_all_v1` 的固定合同。生成接单 front 或具名 front/back PNG。complete 固定全件补全，父必须来自同账成功并结算的 segment；自选件、quick_cap、Smart UV、格式转换及其他厂商参数不能任意添加或静默丢弃。

Meshy 仅五类已有任务的单次 GET，需明确 remote_operation；无生成提交、下载或费用账。来源 metadata 是调用者声明，不能证明服务调用或绕过本地 GLB guard。不自动切厂商、地区或网页路线。

## 海外基础接口

`provider: "tripo-overseas"` 仅 manifest1.1，入口固定 `https://openapi.tripo3d.ai/v3`，动作仅 submit/query/account_balance/download。`h31_image` 接 front PNG，`h31_multiview` 接有序 front/back 双 PNG。模型固定 H3.1 `v3.1-20260211`，wire 六项固定：

```json
{"geometry_quality":"standard","texture":false,"pbr":false,"generate_parts":false,"quad":false,"smart_low_poly":false}
```

最小只读余额 manifest（环境变量名为示例，不填入密钥值）：

```json
{"schema_version":"1.1","provider":"tripo-overseas","operation":"account_balance","credential_ref":{"provider":"tripo-overseas","region":"global","reference":{"kind":"environment","name":"TRIPO_OVERSEAS_API_KEY"}}}
```

公共请求把 manifest 放入 `modeling.run` 的 params.manifest，另给当前 describe 的 implementation_sha256。海外 scope、批准、唯一路径账本与请求 SHA 域独立绑定 provider/region/base/model/wire/输入，不能移用中国站或研究账。无自动 retry、跨区 fallback、batch、resume、migration、bind；下载仅新鲜完整 GLB，必给 max_download_bytes。

金额最多两位小数。2026-10-21 核验的公开无贴图单图/多视图基础价20 credits仅为估价与本地最低预留，不是云端硬扣费上限。实际结算依 credits_consumed；字段缺失保留原预约，不假定零费用。真实余额查询不是收费授权。

## 原账与收费边界

落实操作、冻结输入、模型、单次预留、批次积分、次数及重试边界。批准绑定唯一绝对 ledger_file、精确规范请求 SHA 和上限，只记录真实用户授权；SHA 或 approved:true 不认证批准者身份。已覆盖的步骤继续，未覆盖的新费用/素材/轮数先给可审阅方案。成功但视觉失败也可能收费，充值现金与积分消费分开记录。

accepted task 不再 POST；提交未知保留预约、次数与原请求。不能复制空账、换路径/operation_id 或回滚财务快照重置消费。task_id、规范请求 SHA、wire SHA 和 attempt 身份不可互换。正式账与研究记录格式及哈希语义不同，缺匹配迁移合同就保留原记录并报告缺口。

中国站普通 ledger1.0、legacy1.1 与增量 ledger1.2 各守原范围。legacy1.1 仅旧三类 generation，历史任务不成为 current 后处理父。原生分件仅 manifest1.1/tripo-cn，固定 texture=false/pbr=false/generate_parts=true；文档最低预留40 credits（基础20+分件20）不是永久报价。普通账及真实批准才可执行，不扩张 legacy 范围。原生分件成功父可按批准进入 texture，不能直接作 complete 父。

## 同账追加后处理批准

`approval_append_prepare` / `approval_append_apply` 为纯本地 manifest1.1/tripo-cn 动作。prepare 使用当前原账 source_descriptor 与规范请求 request_descriptor 形成计划/SHA，不改账或创建 approved 文件。apply 另接记录真实批准的 authorization_descriptor，只在原路径追加一个精确请求。生成先取得同账成功结算 current 父，再构造 segment/texture；complete 必须为 segment 父，外部 GET 或历史任务不能冒充。

所有 attempt 须终态且有费用回执，无 halted/unknown，额度与次数仍足够；新增预留不得低于合同最低额或超过剩余额度。旧 operations 有序前缀、上限、厂商、上传范围、历史与财务保持。prepare/apply 不加载凭据、上传、POST、结算或自动重试，也不是动态 DAG。

apply 重读源 SHA，合作路径锁与 FileGuard 守护 CAS，保留完整旧字节及 prepared/applied 回执。替换后异常可能已经写入，先读 approval_recovery、before/expected-after SHA 和备份引用核对当前账；不猜结果或自动回滚。非合作 writer、断电原子性未保证。

## 能力证据概要

| 路线 | 证据 | 限制 |
| --- | --- | --- |
| 中国站 H3.1 基础/原生分件 | 有限真实收费技术链及导入/重开案例 | 不保证完整身份、隐藏人体、独立服装或全部操作实调 |
| 其他中国站固定操作 | 适配器模拟检查及历史服务调用 | 不等于当前公共全操作收费复验 |
| 海外基础 | 模拟检查及一次真实余额 GET | 本次未生成模型、真实扣费或做 Blender/建模测试 |
| Meshy | 已有任务 query 的只读模拟检查 | 真实账号、生成、下载、费用与质量未验/未实现 |
| 本地交接与恢复 | Windows / Blender5.2.1 有限导入、保存/独立重开、UI及受控故障证据 | 不保证任意场景、跨平台、真实CDN续传、云幂等或退款 |

输入/输出用途按实际账号、入口、服务条款和素材来源确认；软件GPL不授予模型权利，不推定独占或其他账号商用许可。仅交接本地模型时不触发云、余额或收费。下一阶段读[生成与比较](generate-and-compare.md)。
