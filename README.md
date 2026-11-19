# Blender Modeling Workflow

将外部模型生成、Blender 候选审阅和制作交接组织为可检查的工作流。项目包含 Blender 插件、配套 CLI 和一个主技能 `blender-modeling-workflow`：CLI 处理请求、预算记录及受支持的云动作；插件读取候选报告，由用户显式采用到新场景；技能按需组织参考、多视图、候选比较、局部整理、材质与交付。

当前版本为 **0.1.14**。原始产物与制作副本应分别保留。参考还原、衣物分件、绑定拓扑与成片质量需按作品验收。

## 能力范围

| 部分 | 当前范围 | 验证边界 |
| --- | --- | --- |
| 本地候选 | 静态、未绑定、嵌入资源 GLB 的后台导入、保存、预览与显式面板采用 | Windows / Blender 5.2.1 LTS 与 5.3.0 的有限案例 |
| Tripo 中国站 | 固定模型生成、分件、贴图、补全及受合同约束的恢复 | H3.1 有真实收费生成→查询→下载→导入/重开案例；其他操作的历史服务调用与公共适配器模拟测试分别记录 |
| Tripo 海外 | 基础 submit/query/account_balance/download；H3.1 单图或正背双图标准无贴图几何 | 模拟适配器测试及一次真实余额 GET；未执行海外模型生成或 Blender 验收 |
| Meshy | 五类已有任务端点的单次 query | 只读模拟测试；未实现提交、下载或费用账 |

详见 [能力与限制](docs/CAPABILITIES.md) 和 [凭据、费用与恢复](docs/SECURITY.md)。各提供商必须显式选择，账号、授权、任务和财务账不能跨提供商或地区复用。

## 使用前

源码包包含匹配的建模专用 CLI。执行支持 Windows 和 Python 3.12+。本地交接已验证 Blender 5.2.1 LTS 与 5.3.0，运行时由用户提供。

```powershell
python -B scripts/modeling_workflow.py --version
python -B scripts/modeling_workflow.py modeling describe
python -B scripts/modeling_workflow.py modeling describe --provider tripo-overseas --provider-operation account_balance --schema
```

入口也可使用 `blenderctl.ps1`，由 `BLENDERCTL_PYTHON` 指定已有Python。先通过 `modeling describe` 查看动作Schema、提供商合同和实现身份，再构造请求。CLI仅包含建模和作业管理，不包含其他专项工具。

从 [Releases](https://github.com/tsist/blender-modeling-workflow/releases) 下载插件ZIP、技能ZIP和独立源码ZIP。插件通过Blender的Install from Disk安装并启用；面板采用后需显式保存新文件。完整技能安装到代理实际使用的技能根，保留单一主入口及其references。具体命令见 [安装指南](docs/INSTALL.md)。

本次在独立 Blender 配置实测本地导入与采用。已有生产案例、离线回归和支持范围见 [验证说明](docs/VERIFICATION.md)。CI 运行离线模拟与打包检查。凭据、未知提交和费用处理见 [安全说明](docs/SECURITY.md)。依赖与来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可

软件与配套技能采用 **GPL-3.0-or-later**，见 `LICENSE`。该软件许可不授予参考图、第三方素材或生成模型的使用权；输入外发和输出用途须满足实际账号、服务条款与素材授权。项目不承诺独占权、服务可用性、固定价格或通用模型质量。
