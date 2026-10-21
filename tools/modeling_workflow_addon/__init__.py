"""External model workflow with a shared API core and explicit scene handoff."""
bl_info = {
    'name': 'Modeling Workflow', 'author': 'Blender AI', 'version': (0, 1, 13),
    'blender': (5, 2, 0), 'location': 'View3D > Sidebar > Modeling Workflow',
    'description': 'Review generated model candidates and adopt into a new scene',
    'category': 'Object',
}


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
