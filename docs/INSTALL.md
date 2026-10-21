# 安装与首次检查

## 两种组件

从本仓 Release 下载 `modeling-workflow-addon-0.1.13.zip`，通过 Blender Preferences → Get Extensions → Install from Disk 安装并启用。在3D View侧栏的 Modeling Workflow 面板选择已验候选报告，读取后显式采用到新场景，再另存 `.blend`。面板不上传图像或提交云任务。不要打开未保存工作的另一个文件来检查插件。

云动作与后台本地导入使用源码包中的 CLI。解压 `blender-modeling-workflow-0.1.13-source.zip` 或 clone 本仓后即可运行，无需母项目、submodule 或 pip 依赖。当前执行支持 Windows；需要已有 Python3.12+。本地导入另需已有 Blender5.2.x，并显式提供二进制路径。本轮基础发行验证没有启动 Blender。

```powershell
$env:BLENDERCTL_PYTHON = '<已有Python的绝对路径>'
& '.\blenderctl.ps1' --version
& '.\blenderctl.ps1' modeling describe
& '.\blenderctl.ps1' modeling describe --provider tripo-overseas --provider-operation account_balance --schema
```

也可直接运行 `python -B scripts/modeling_workflow.py modeling describe`。这个发行 CLI 仅暴露建模及其作业管理；未包含材质、灯光、几何节点等其他专项命令。发现与Schema检查不调用厂商、不启动Blender、不创建生成任务。

## 技能

把 `blender-modeling-workflow-skill-0.1.13.zip` 解压到代理实际使用的技能根，确保结果是 `blender-modeling-workflow/SKILL.md` 与同目录 references。源码提供一个显式安装器：

```powershell
& $env:BLENDERCTL_PYTHON -B scripts/install_skill.py --destination '<代理技能根绝对路径>'
```

目标已有同名目录时安装器拒绝覆盖；先自行比较、备份或选择不同目录。本仓不修改用户全局配置。为技能设置 `BLENDERCTL_ROOT` 为本仓绝对路径；执行时另以 `BLENDERCTL_PYTHON` 选择已有Python。重启或刷新代理技能列表按实际代理方式进行。

## 请求与作业

公共UTF-8 JSON请求的顶层为 `schema_version`、`command`、`params`。先从当前describe取得Schema及 `implementation_sha256`；可以在params显式固定该值。源码变化会使旧值被拒绝；重新审阅并发现，不能替换旧付费请求指纹后重放。

只读海外余额manifest示例：

```json
{"schema_version":"1.0","command":"modeling.run","params":{"manifest":{"schema_version":"1.1","provider":"tripo-overseas","operation":"account_balance","credential_ref":{"provider":"tripo-overseas","region":"global","reference":{"kind":"environment","name":"TRIPO_OVERSEAS_API_KEY"}}}}}
```

通过系统安全方式配置该环境变量；示例里只有变量名，不填密钥值。不要把凭据或生成请求加入Git。保存请求到仓库外的明确路径，运行：

```powershell
& '.\blenderctl.ps1' --jobs-dir '<独立作业目录绝对路径>' --timeout 70 --compact request '<请求JSON绝对路径>'
```

本地导入还要给全局 `--blender '<blender.exe绝对路径>'`；使用 `modeling describe --operation import_local --schema` 取得完整字段。`--async` 创建后台作业；以相同jobs-dir调用 `job wait <job_id> --wait-seconds 30` 获取有界快照。`job cancel`只请求停止本地作业，不能推定云任务取消或退款。完整结果在作业目录，`--compact`只缩减显示。

## 费用与升级

云提交仍要求具体素材和费用授权；余额不等于允许消费。海外与中国站的凭据、批准、账及任务保持隔离。未知POST、上传未决或缺少终态费用时保留预约，不自动重提、清账或退款。升级先保留旧软件和任务证据，软件回退不能回退财务记录。详见 [安全说明](SECURITY.md)。
