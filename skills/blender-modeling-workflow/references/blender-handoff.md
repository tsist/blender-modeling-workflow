# 外部候选导入、审查与采用

把外部三维文件接进原生 Blender 工程时，先按来源挑对入口，让工具自动生成带指纹的候选工程并完成冷重开验证，再决定是否采用到目标工程。

## 路线一：接收 Studio 静态 FBX（保留原角点法线）

手头已有 Studio 导出的静态几何二进制 FBX 与同次完成导出清单时，走工作区 `studio_fbx` 直入合同，完整保留原角点法线、平滑与锐边标志。

1. 先查实机 Schema 确认字段：
   ```powershell
   $ctl = './blenderctl.ps1'
   & $ctl modeling describe --operation studio_fbx --schema
   ```
2. 写出 UTF-8 参数文件：外层放 `manifest` 和 `studio_export`。`manifest` 填 `schema_version: "1.0"`、`operation: "import_local"` 和原 FBX 的 `input.file`、`input.expected_sha256`；`studio_export` 填导出清单的 `file`、`expected_sha256`、`project_id`、`operator_id` 和 `download_job`。
3. 提交导入并等终态：
   ```powershell
   & $ctl --jobs-dir $jobs modeling run --params $paramsFile
   ```
4. 查报告里的 `input_adapter`（`STUDIO_FBX_STATIC_V1`）、候选 `.blend` 路径、场景名和 SHA256，留给下一步审查和采用。这类静态几何 FBX 重点交接形体和法线，UV 与 PBR 交下游按成品用途补齐。

### 通用静态 FBX 导入

没有 Studio 导出清单的普通静态 FBX，直接走公共 `exchange import`。写一份 JSON 清单标明 `format: "FBX"`、`mode: "STATIC"`、场景名、帧率与单位，再运行：

```powershell
& $ctl --jobs-dir $jobs exchange import $fbx --expected-sha256 $expectedSha --manifest $importManifest
```

静态交换模式会三角化网格并过滤贴图块，导入完成后另起进程跑 `model inspect` 核对对象与 UV 缺项。

## 路线二：接收本地静态 GLB

已落盘的静态 GLB 通过 `--modeling-release 0.1.14` 跑 `import_local` 导入，一次完成贴图打包、保存重开校验与多视角预览。

1. 查 Schema 与帮助确认字段：
   ```powershell
   & $ctl --modeling-release 0.1.14 modeling describe --operation import_local --schema
   & $ctl --modeling-release 0.1.14 modeling run --help
   ```
2. 构造参数文件并异步提交。把 `--modeling-release 0.1.14` 和 `--jobs-dir` 写在子命令前面：
   ```powershell
   $inputSha = (Get-FileHash -LiteralPath $glbFile -Algorithm SHA256).Hash.ToLowerInvariant()
   $params = @{
     manifest = @{
       schema_version = '1.0'
       operation      = 'import_local'
       input          = @{ file = $glbFile; expected_sha256 = $inputSha }
       provenance     = @{ provider = 'tripo-cn' }
       preview        = @{
         enabled    = $true
         front_axis = '+X'
         engine     = 'BLENDER_WORKBENCH'
         width      = 640
         height     = 960
         samples    = 16
         views      = @('front', 'back', 'threequarter')
       }
     }
   }
   $params | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $paramsFile -Encoding utf8
   & $ctl --modeling-release 0.1.14 --jobs-dir $jobs --async modeling run --params $paramsFile
   ```
3. 拿到 `job_id` 后用同一 `--modeling-release 0.1.14` 和 `--jobs-dir` 跑一次有界等待：
   ```powershell
   & $ctl --modeling-release 0.1.14 --jobs-dir $jobs job wait $jobId --wait-seconds 30
   ```
4. 核对结果报告：检查退出码、`ok`、候选 `.blend` 绝对路径与 `candidate_sha256`，确认 `save_reopen`（同进程）和 `fresh_process_reopen`（新进程）两项都为 `pass`，再打开 `previews` 里的图像看实际朝向。

手头同时有 Studio 下载清单和 GLB 时，先用工作区 `blender_handoff.ps1` 传入 `-InputFile`、`-TaskDir`、`-DownloadManifest` 和 `-ExpectedDownloadManifestSha256`，拿到外层 `report_path` 与 SHA256 后，交给下面公共入口审查和采用。

## 路线三：接收 Studio Bridge 原生候选

通过官方 Studio Bridge 收到的原生 `.blend` 候选，先经公共交接提取并核对，再带进当前工程。

1. 用 `& $ctl discover --action bridge.handoff` 查接收入口并读取 `handoff.json`，按清单所在目录解析相邻 `candidate.blend`、核对 `sha256`、Studio 变体和对象名。详见 [Studio Bridge 交接](https://github.com/tsist/tripo-studio-opencli/blob/main/skills/tripo-studio-opencli/references/studio-blender-bridge.md)。
2. 遇到旧版本 Blender 保存的候选，先跑 `project prepare-copy` 并选 `version_policy=UPGRADE` 升到当前工作副本，取新副本路径与 SHA256 继续制作。
3. 在当前工作区保持公共 CLI 与内置桥接通道干净，让 `localhost:9876` 留给工作区桥接服务，所有交接统一走 `blenderctl` 和内置插件。

## 审查并采用到目标工程

无论候选来自本地 GLB、Studio 静态 FBX 还是 Bridge，采用前都先走公共审查，再追加到目标工程的新副本里。先保存目标工程里已有的手调内容，再算 SHA256。

```powershell
# 1. 复核导入报告、原清单与候选指纹
& $ctl --jobs-dir $jobs modeling review-candidate $report --expected-report-sha256 $reportSha

# 2. 追加为目标工程新副本中的活动场景
& $ctl --jobs-dir $jobs modeling adopt-candidate $targetBlend --expected-sha256 $targetSha --report $report --expected-report-sha256 $reportSha
```

- 绑定旧独立 Studio 磁盘交接清单时，在两条命令上同时传入 `--studio-export-manifest $manifest --expected-studio-export-sha256 $manifestSha`。
- 指定采用前活动场景时，在 `adopt-candidate` 上加 `--scene <场景名>`。
- 采用后查看返回里的 `data.output`（新工程路径与 SHA）、`data.scene`（候选场景实际名）和 `data.bridge_id_map`（重名后的本地对象、网格、材质与图像映射）。
- 在图形界面里用侧栏 Modeling Workflow 面板读 `modeling-report.json` 时，核对两项重开 `pass` 和候选 SHA，切到物体模式采用到新场景，再另存为持久制作路径。没有安装面板时，直接交付独立 `.blend` 与报告。

## 几何体检与导入核对要点

采用或导入完成后，立刻对候选 `.blend` 跑一次只读体检并打开真图核对：

```powershell
& $ctl --jobs-dir $jobs --compact model inspect $candidate --expected-sha256 $candidateSha --qa-profile game
```

重点盯这四项：

1. **坐标系与尺度**：外部生成件常以 `+X` 朝正、`+Z` 朝上且高度约 `1.0 m`。多候选拼装或跨源比较前先核对朝向和真实尺寸；相机 `front_axis` 按模型实际正面填写，朝向偏差时调显示或另存副本修正，已有绑定的模型先留住原变换。
2. **GLB 拆点与边界统计**：glTF 会在 UV 缝线和硬边法线处拆开顶点，让原始索引边界边数看起来偏高。体检时把「原始索引边界」「按位置分组后的流形诊断」和「肉眼可见孔洞」分开记录，保留原网格，确认真有缺口再修。
3. **贴图色彩空间与 ORM 通道**：逐条看节点实际连线，把 `Base Color` 保持为 `sRGB`，法线、粗糙度、金属度以及拆包后的 ORM 单通道设为 `Non-Color`，清查空占位槽位。
4. **换光看真图**：Workbench 几何白模图看大形和轮廓，判断 PBR、高光、透明和法线贴图时切到 Cycles 或项目现有渲染接口。改过灯光参数后，打开新解码的图核对画面确实变化。

## 多候选同条件比较与交付冷重开

同时评估多个候选时，锁定同一套视角、构图、灯光、渲染引擎和色彩管理，集中看正背、四分之三、脸部与手袖接合局部。每份候选记清来源文件、SHA256、可用用途、主要差距以及下一轮该局部修还是重建。

准备交付或转交下游前做完三步收尾：

1. **独立新进程冷重开**：每次移动目录或编辑保存后，对最终路径另起后台进程跑 `inspect` 或 `project verify`，重新核对尺寸、单位、轴向、对象、共享数据、UV、材质和外部资源，再复渲代表视角打开看图。
2. **明确外部资源归属**：分别列出打包图像（packed）与外链路径。调整相对路径时先建好目标文件位置再设置路径，保存后重开逐张复查。
3. **整理交接索引**：写明每个交付文件的相对路径、SHA256、用途、来源与上游父候选；记录实际使用的 `--jobs-dir`、`job_id`、远端 `task_id`、最佳候选和下一步动作，过滤掉密钥、签名链接和授权头。把技术体检、实际观感、部件可编辑性和用户艺术选用分开写清，方便审看拍板。
