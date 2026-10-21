# 本地模型交接到 Blender

已持久GLB用import_local，不需要云、凭据、余额或新收费；在独立后台候选导入，检查网格/UV/材质/图像，打包、保存及独立重开。已有`.blend`从实际检查、局部制作或[交付](delivery-and-review.md)进入，import_local不接`.blend`。

## 定位与发现

先把BLENDERCTL_ROOT设为仓库根，BLENDERCTL_PYTHON设为已有Python3.12+的绝对路径；另以用户已安装Blender设置`$modelingBlender`，为本次发现设置独立绝对`$modelingDiscoveryJobsDir`。本发行CLI为0.1.13；本地交接目标Windows/Blender5.2.x，已有实证集中5.2.1，其他平台/版本未据此通过。

```powershell
$ErrorActionPreference = 'Stop'
$modelingCliRoot = $env:BLENDERCTL_ROOT
$modelingPython = $env:BLENDERCTL_PYTHON
if ([string]::IsNullOrWhiteSpace($modelingCliRoot) -or [string]::IsNullOrWhiteSpace($modelingPython)) { throw '先定位仓库根与已有 Python 3.12+' }
$modelingCli = Join-Path $modelingCliRoot 'blenderctl.ps1'
$modelingCliSource = Join-Path $modelingCliRoot 'tools\blenderctl\cli.py'
if (-not (Test-Path -LiteralPath $modelingCliSource -PathType Leaf)) { throw 'CLI 源入口不存在' }
& $modelingPython -B $modelingCliSource --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $modelingPython -B $modelingCliSource --blender $modelingBlender --jobs-dir $modelingDiscoveryJobsDir modeling describe --operation import_local --schema
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $modelingPython -B $modelingCliSource --blender $modelingBlender --jobs-dir $modelingDiscoveryJobsDir modeling run --help
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

根wrapper支持BLENDERCTL_PYTHON，也可直接使用上面的Python源入口。技能独立分发时先定位用户已有配套CLI，不假定相对技能目录自带CLI。全局--blender/--jobs-dir放子命令前；本发行没有通用事务入口，不依赖BLENDER_PATH或默认目录。

## 构造与验证候选

从当前Schema构造UTF-8 params，输入描述符含绝对file和expected_sha256；声明真实provider/provenance，不能用metadata代替来源证明。preview的front_axis、views、engine和分辨率按实际输入及合同选择，不照抄模型朝向。公共request包裹schema_version/command='modeling.run'/params，携当前describe的implementation_sha256，不猜指纹。需要云以外的精细编辑时先说明所需外部Blender能力。

复杂输入先检查GLB合同：静态未绑定、资源嵌入、最大256MiB；required glTF extensions、骨骼/动画及其他不支持语义保持拒绝，不换脚本绕过已知guard。单位、轴向、负缩放及来源未知项单列。

返回job_id后沿同jobs-dir一次有界wait或result，queued只证明受理，running时继续独立工作，不重复提交/循环轮询。核对终态、退出码、ok/error/artifacts。报告candidate绝对路径与candidate_sha256定位`.blend`；previews含view/file/SHA/尺寸/引擎，不预设候选文件名。save_reopen和fresh_process_reopen均pass才进入采用，二者不能互替。

核对对象/共享数据、世界尺寸、法线、UV、槽、打包图像和依赖，解码查看实际图像方向。错向时在副本调整显示/预览并另存，保留原模型。分件逐件孤立看隐藏表面及衣层；当前公共preview无ROI/隔离字段，需要外部诊断时明确范围。Workbench只证明几何预览，PBR等用支持的实际渲染入口。不同命令的samples等字段看各自Schema，不把preview参数套另一个渲染合同。

文件/SHA无效、重开失败或已知保护拒绝时保留原件和失败。输出候选/SHA、来源、已看图像与缺口后进入[分件](parts-and-completion.md)、[材质](material-cooperation.md)或[交付](delivery-and-review.md)。

## 面板读取与显式采用

Blender已安装启用插件时，在3D View侧栏Modeling Workflow读取modeling-report.json，核对两种重开pass及candidate/report SHA。摘要数量不证明语义部件完整。用户选用后在Object Mode采用到新场景；原场景保留，动作不自动保存，需另存持久制作路径。未安装面板可交付独立`.blend`/报告，不自动安装；报告/候选变化就重新读取。

采用后确认活动Scene/相机，视口没框住模型不直接判失败。新进程查原/候选Scene、各层选择/active、共享数据、图像及来源。需要证明原场景保护时先留数值基线，成员存在不能证明属性全保持。非活动Scene世界矩阵先切目标、更新ViewLayer/依赖图再比较。技术验收与visual pending艺术选用分别报告。
