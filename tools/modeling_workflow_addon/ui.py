"""Local candidate review UI. Credentials and cloud submissions stay out of blend."""
import json
from pathlib import Path
import bpy
from bpy.props import StringProperty, PointerProperty
from . import core

PROPERTY = 'modeling_workflow_ui_v1'


class MWSettings(bpy.types.PropertyGroup):
    report_file: StringProperty(name='候选报告', subtype='FILE_PATH')
    reviewed_sha256: StringProperty(options={'HIDDEN'})
    summary: StringProperty(name='检查结果')
    last_message: StringProperty(name='最近操作')


def settings(context):
    return getattr(context.scene, PROPERTY)


class MW_OT_read_report(bpy.types.Operator):
    bl_idname = 'modeling_workflow.read_report'
    bl_label = '读取并核对候选'
    bl_description = '核对候选 SHA、保存重开与独立检查结果，不修改模型'

    def execute(self, context):
        state = settings(context)
        try:
            report, sha = core.load_report(bpy.path.abspath(state.report_file))
            content = report['content']
            state.reviewed_sha256 = sha
            state.summary = json.dumps({'meshes': len(content['meshes']),
                'triangles': sum(m['triangles'] for m in content['meshes']),
                'images': len(content['images']), 'visual_review': report.get('visual_review', 'pending'),
                'task_id': report.get('provenance', {}).get('task_id', '')})
            state.last_message = '技术检查通过；采用和艺术验收由你决定。'
            return {'FINISHED'}
        except Exception as error:
            state.reviewed_sha256, state.summary = '', ''
            state.last_message = str(error)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class MW_OT_adopt(bpy.types.Operator):
    bl_idname = 'modeling_workflow.adopt_candidate'
    bl_label = '采用到新场景'
    bl_description = '复制已核对的候选到新场景；原场景保留，不自动保存文件'

    @classmethod
    def poll(cls, context):
        return bool(context.scene and settings(context).reviewed_sha256 and context.mode == 'OBJECT')

    def execute(self, context):
        state = settings(context)
        try:
            path, sha = bpy.path.abspath(state.report_file), state.reviewed_sha256
            result = core.adopt_candidate(path, sha)
            new_state = settings(context)
            new_state.report_file, new_state.reviewed_sha256 = path, sha
            new_state.summary = state.summary
            new_state.last_message = '已采用到 ' + result['scene'] + '；原场景 ' + result['previous_scene'] + ' 保留，请另存。'
            return {'FINISHED'}
        except Exception as error:
            state.last_message = str(error)
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class MW_PT_panel(bpy.types.Panel):
    bl_label = 'Modeling Workflow'
    bl_idname = 'MW_PT_modeling_workflow'
    bl_space_type, bl_region_type, bl_category = 'VIEW_3D', 'UI', 'Modeling Workflow'

    def draw(self, context):
        layout, state = self.layout, settings(context)
        layout.label(text='外部建模候选 · 本地审阅')
        layout.prop(state, 'report_file')
        layout.operator('modeling_workflow.read_report')
        if state.summary:
            try:
                summary = json.loads(state.summary)
                layout.label(text=f"网格 {summary['meshes']} · 三角面 {summary['triangles']:,}")
                layout.label(text=f"打包贴图 {summary['images']} · 视觉 {summary['visual_review']}")
            except (ValueError, KeyError):
                layout.label(text='请重新读取候选报告')
        layout.operator('modeling_workflow.adopt_candidate')
        layout.label(text='原场景保留；不自动保存。', icon='INFO')
        if state.last_message:
            box = layout.box()
            for line in [state.last_message[i:i + 28] for i in range(0, len(state.last_message), 28)]:
                box.label(text=line)


CLASSES = (MWSettings, MW_OT_read_report, MW_OT_adopt, MW_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    setattr(bpy.types.Scene, PROPERTY, PointerProperty(type=MWSettings))


def unregister():
    if hasattr(bpy.types.Scene, PROPERTY):
        delattr(bpy.types.Scene, PROPERTY)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
