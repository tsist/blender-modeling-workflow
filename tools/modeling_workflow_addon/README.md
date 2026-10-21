# Modeling Workflow 0.1.13

Blender 本地候选审阅插件，配套 `blender-modeling-workflow` 技能与源码 CLI。插件读取候选报告、核对文件 SHA 和技术检查结果，并将确认后的候选复制到新场景。面板不保存凭据，不直接调用云生成，不自动替换原场景或保存工程。

项目源码与发行说明：[tsist/blender-modeling-workflow](https://github.com/tsist/blender-modeling-workflow)。本说明随插件 ZIP 提供，使用插件不依赖仓库外的相对文档链接。

## 安装与使用

1. 准备 Blender 5.2 或更高版本；历史已验环境为 Windows / Blender 5.2.1 LTS。其他版本和平台须单独验证。
2. 在 Blender 的 Preferences → Get Extensions → Install from Disk 选择插件 ZIP，并启用 Modeling Workflow。不要将源码仓库 ZIP 当成插件安装包。
3. 在 3D View 按 N，打开 Modeling Workflow 面板，选择匹配的 `modeling-report.json`，点击“读取并核对候选”。报告须指向持久保存且已检查的候选；普通 GLB 不直接作为候选报告读取。
4. 在 Object Mode 点击“采用到新场景”。插件追加并切换到候选场景；原场景保留。查看结果后用 Blender 原生 Save As 另存新工程。

读取报告会更新当前场景的插件检查状态。采用并非事务回滚接口；失败后先检查是否已追加场景，不要连续重复点击。候选文件或报告改变后重新读取并核对。技术检查通过不等于视觉质量、绑定或动画验收通过。

## 源码 CLI 与技能依赖

云操作、GLB 导入和后台检查由匹配的公共 CLI 执行。插件 ZIP 不捆绑 CLI、Python 或 Blender，不自动下载、安装或启用依赖。需要云流程时从项目源码取得完整匹配版本；不能只拷贝几个 modeling 文件。

源码 CLI 需要 Windows、已有 Python 3.12+ 和用户指定的 Blender；FileGuard/PathLocks 使用 Windows 系统接口，其他系统的 CLI 能力未验证。在仓库根可使用既有 Python 执行：

```text
python -B tools/blenderctl/cli.py modeling describe
python -B tools/blenderctl/cli.py modeling describe --provider tripo-overseas
```

实际作业须指定独立作业目录，本地导入另需显式指定 Blender；此发行不提供通用事务入口。用当前帮助、Schema 和 `implementation_sha256` 确认接口。配套主技能 `blender-modeling-workflow` 负责阶段组织和有界交接，不提供账号、余额或收费授权。

## 支持范围与验证边界

- 本地交接只接静态、未绑定、嵌入资源的 GLB v2，最大 256 MiB。required glTF extensions、骨骼、动画、外部依赖及其他未支持语义会拒绝或需要专项适配；不保证任意 GLB 无损往返。
- `tripo-cn` 保留已有固定操作、预算、查询和有限恢复合同；有历史真实单项生成、下载、导入与独立重开案例。有限案例不证明全部模型、后处理、语义分件或跨类型生产质量。
- `tripo-overseas` 使用显式 manifest 1.1、global 凭据 scope、独立批准、账本和请求 SHA 域。基础动作仅 `submit`、`query`、`account_balance`、`download`；生成仅 H3.1 的单 front 或 front/back PNG，固定标准无贴图选项。验证限于受控模拟收费路径及一次真实只读余额 GET；本版未执行海外模型生成、实际扣费、Blender 模型导入或视觉质量测试。
- `meshy` 仅支持已知任务的只读查询适配，离线测试不等于真实账号或生成链通过；不提供提交、下载或 Tripo 预算复用。

提供商名称、任务 ID、预算、凭据及下载身份均不得跨区或跨厂商互换。当前 `modeling describe` 决定精确字段与可执行动作，不自动切换厂商或参数。当前发布不承诺自动绑定、通用可动画拓扑、完整服装层次或自动材质完成。

## 预算与恢复安全

收费前必须有明确的操作、参考素材外发范围、规范请求 SHA、唯一账本、单次预留及总额/次数批准。余额 GET 只观察账号，不构成收费授权；服务价格和本地预留也不是云端硬扣费保证。凭据仅用环境变量或受支持的安全引用，不写进 `.blend`、请求正文、报告或仓库。

上传未决、提交结果未知或终态缺实际费用时保留预约和次数。已受理任务按原任务查询，禁止再次 POST、复制空账、手改费用、清除 unknown 或自动重试。海外基础版不支持 batch、resume、migration、bind 或中国站 checkpoint 复用；中国站恢复只在其精确合同与原有授权范围内执行。中断后保留原请求、脱敏回执和本地 partial，先查明状态。

## 许可

软件与配套技能使用 GPL-3.0-or-later，完整条款见本包 LICENSE。原始参考和生成资产的权利不由软件 GPL 授予；使用者须依据实际账号、服务入口、现行条款及参考来源确认用途。插件不授予服务访问、商业使用、独占权或服务转售权。
