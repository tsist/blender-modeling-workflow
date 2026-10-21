# 来源与依赖

本项目将建模插件、一个配套技能和建模专用 CLI 放在同一仓库。CLI 从项目作者维护的 [blenderctl](https://github.com/tsist/blenderctl) 公共实现提取建模所需的合同、文件锁、进程树、作业和 worker 路径；发行适配仅暴露建模命令。保留原有预算、未知提交与不可变输入保护。没有捆绑其他专项工具、服务或第二套财务系统。

软件与技能按 GPL-3.0-or-later 分发，完整文本见根目录及产品包内 LICENSE。公共 blenderctl 的 LICENSE 已核对为 GPLv3。该来源说明不表示公共仓库的旧版本包含本次所有新增功能；应使用本仓匹配源码和当前 describe 输出。

运行时使用现有 Python 3.12+ 的标准库；本地候选交接使用用户已有的 Blender 5.2.x 及其 bpy/mathutils。Windows 文件锁与进程管理调用系统 API。本仓不下载或捆绑 Python、Blender、第三方 SDK 或生成模型。

Tripo 与 Meshy 是可选网络服务。它们的账号、配额、价格、条款、输入外发与输出权利由使用者自行确认；软件许可证不提供服务访问许可，也不授予参考图或生成资产权利。
