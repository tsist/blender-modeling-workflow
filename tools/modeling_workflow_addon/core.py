"""Native GLB handoff. No provider calls, no flattening, no global preferences.

create_candidate and verify_candidate are background-only. The UI may only
append a verified candidate as a new scene; it never resets an existing scene.
"""
from array import array
import hashlib
import json
import math
from pathlib import Path
import struct

import bpy
from mathutils import Vector

VERSION = '0.1.13'
OWNER = 'modeling_workflow_v1'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    if path.exists():
        raise ValueError('Output already exists: ' + str(path))
    temp = path.with_suffix(path.suffix + '.partial')
    with temp.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
    temp.rename(path)


def preflight_glb(path):
    """Same conservative embedded-resource boundary as public exchange GLB."""
    path = Path(path)
    size = path.stat().st_size
    if path.suffix.lower() != '.glb' or size < 20 or size > 256 * 1024 * 1024:
        raise ValueError('Expected GLB v2 within 256 MiB')
    with path.open('rb') as stream:
        magic, version, total = struct.unpack('<4sII', stream.read(12))
        length, kind = struct.unpack('<I4s', stream.read(8))
        if magic != b'glTF' or version != 2 or total != size or kind != b'JSON' or length > size - 20:
            raise ValueError('Invalid GLB container')
        doc = json.loads(stream.read(length))
    if doc.get('extensionsRequired'):
        raise ValueError('Required glTF extensions need a dedicated adapter')
    for item in [*doc.get('buffers', []), *doc.get('images', [])]:
        if 'uri' in item and not item['uri'].startswith('data:'):
            raise ValueError('Only embedded GLB resources are supported')
    if doc.get('animations') or doc.get('skins'):
        raise ValueError('This candidate workflow currently accepts static unrigged GLB only')
    return doc


def data_hash(collection, property_name, code, width):
    values = array(code, [0]) * (len(collection) * width)
    collection.foreach_get(property_name, values)
    return hashlib.sha256(values.tobytes()).hexdigest()


def _socket_value(socket):
    value = getattr(socket, 'default_value', None)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    try:
        return list(value)
    except TypeError:
        return getattr(value, 'name', None)


def snapshot(scene=None):
    """Preservation evidence after native import, not source semantic equivalence."""
    scene = scene or bpy.context.scene
    meshes, used_materials = [], set()
    for obj in sorted(scene.objects, key=lambda item: item.name):
        if obj.type != 'MESH':
            continue
        mesh = obj.data
        sizes = array('i', [0]) * len(mesh.polygons)
        mesh.polygons.foreach_get('loop_total', sizes)
        meshes.append({
            'object': obj.name, 'mesh': mesh.name,
            'parent': obj.parent.name if obj.parent else None,
            'matrix_world': [list(row) for row in obj.matrix_world],
            'vertices': len(mesh.vertices), 'faces': len(mesh.polygons),
            'triangles': sum(max(n - 2, 0) for n in sizes),
            'geometry_sha256': data_hash(mesh.vertices, 'co', 'f', 3),
            'loop_vertices_sha256': data_hash(mesh.loops, 'vertex_index', 'i', 1),
            'polygon_sizes_sha256': hashlib.sha256(sizes.tobytes()).hexdigest(),
            'uv': [{'name': uv.name, 'sha256': data_hash(uv.data, 'uv', 'f', 2)} for uv in mesh.uv_layers],
            'materials': [s.material.name if s.material else None for s in obj.material_slots],
            'modifiers': [m.type for m in obj.modifiers],
            'shape_keys': [k.name for k in mesh.shape_keys.key_blocks] if mesh.shape_keys else [],
        })
        used_materials.update(s.material for s in obj.material_slots if s.material)
    materials, images = [], set()
    for mat in sorted(used_materials, key=lambda item: item.name):
        nodes, links = [], []
        if mat.node_tree:
            for node in sorted(mat.node_tree.nodes, key=lambda item: item.name):
                row = {'name': node.name, 'type': node.bl_idname,
                       'inputs': {s.identifier: _socket_value(s) for s in node.inputs}}
                if node.type == 'TEX_IMAGE':
                    row.update(image=node.image.name if node.image else None,
                               interpolation=node.interpolation, extension=node.extension)
                    if node.image:
                        images.add(node.image)
                nodes.append(row)
            links = sorted([l.from_node.name, l.from_socket.identifier,
                            l.to_node.name, l.to_socket.identifier] for l in mat.node_tree.links)
        materials.append({'name': mat.name, 'diffuse_color': list(mat.diffuse_color),
                          'use_nodes': mat.use_nodes, 'nodes': nodes, 'links': links})
    image_rows = []
    for image in sorted(images, key=lambda item: item.name):
        if not image.packed_file:
            raise ValueError('Candidate has an unpacked image dependency: ' + image.name)
        image_rows.append({'name': image.name, 'size': list(image.size), 'source': image.source,
                           'color_space': image.colorspace_settings.name, 'packed': True,
                           'packed_sha256': hashlib.sha256(image.packed_file.data).hexdigest()})
    return {'meshes': meshes, 'materials': materials, 'images': image_rows,
            'objects': [{'name': o.name, 'type': o.type,
                         'parent': o.parent.name if o.parent else None,
                         'matrix_world': [list(row) for row in o.matrix_world]}
                        for o in sorted(scene.objects, key=lambda item: item.name)
                        if not o.get('mw_preview')]}


def _bounds(scene):
    points = [o.matrix_world @ Vector(c) for o in scene.objects if o.type == 'MESH' for c in o.bound_box]
    if not points or not all(math.isfinite(v) for point in points for v in point):
        raise ValueError('Candidate needs a finite mesh bounding box')
    low = Vector([min(c[i] for c in points) for i in range(3)])
    high = Vector([max(c[i] for c in points) for i in range(3)])
    if high.z - low.z <= 0:
        raise ValueError('Candidate must have positive height along +Z')
    return low, high


def setup_preview(scene, spec):
    low, high = _bounds(scene)
    center = (low + high) / 2
    extent = high - low
    scale = max(extent)
    front = Vector({'+X': (1, 0, 0), '-X': (-1, 0, 0),
                    '+Y': (0, 1, 0), '-Y': (0, -1, 0)}[spec['front_axis']])
    side = front.cross(Vector((0, 0, 1)))
    directions = {'front': front, 'back': -front, 'side': side,
                  'threequarter': (front + 0.75 * side + Vector((0, 0, 0.12))).normalized()}
    cameras = {}
    for view in spec['views']:
        data = bpy.data.cameras.new('MW_' + view)
        obj = bpy.data.objects.new(data.name, data)
        scene.collection.objects.link(obj)
        obj['mw_preview'] = True
        obj.location = center + directions[view] * scale * 3
        obj.rotation_euler = (center - obj.location).to_track_quat('-Z', 'Y').to_euler()
        data.type = 'ORTHO'
        # Project actual bbox onto camera axes; diagonal models retain margin.
        rotation = obj.rotation_euler.to_matrix()
        right, up = rotation @ Vector((1, 0, 0)), rotation @ Vector((0, 1, 0))
        horizontal = sum(abs(right[i]) * extent[i] for i in range(3))
        vertical = sum(abs(up[i]) * extent[i] for i in range(3))
        data.ortho_scale = max(vertical, horizontal * spec['height'] / spec['width']) * 1.15
        data.clip_start, data.clip_end = scale * 0.001, scale * 10
        cameras[view] = obj.name
    scene.camera = bpy.data.objects[cameras[spec['views'][0]]]
    scene.render.engine = spec['engine']
    scene.render.resolution_x, scene.render.resolution_y = spec['width'], spec['height']
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.image_settings.color_depth = '8'
    scene.render.film_transparent = False
    scene.view_settings.exposure, scene.view_settings.gamma = 0, 1
    scene.world = bpy.data.worlds.new('MW_ReviewWorld')
    if spec['engine'] == 'CYCLES':
        scene.cycles.device = 'CPU'
        scene.cycles.samples = spec['samples']
        scene.cycles.use_denoising = True
        scene.view_settings.view_transform = 'AgX'
        scene.world.use_nodes = True
        background = next(n for n in scene.world.node_tree.nodes if n.type == 'BACKGROUND')
        background.inputs['Color'].default_value = (0.055, 0.055, 0.055, 1)
        background.inputs['Strength'].default_value = 0.6
        for name, offset, energy, size in (
            ('Key', 1.3 * front + 1.1 * side + Vector((0, 0, 1.4)), 130, 1.6),
            ('Fill', front - 1.4 * side + Vector((0, 0, 0.6)), 90, 1.7),
            ('Rim', -1.1 * front + Vector((0, 0, 1.3)), 180, 1.4),
        ):
            data = bpy.data.lights.new('MW_' + name, 'AREA')
            data.energy, data.shape, data.size = energy * scale * scale, 'DISK', size * scale
            obj = bpy.data.objects.new(data.name, data)
            scene.collection.objects.link(obj)
            obj['mw_preview'] = True
            obj.location = center + offset * scale
            obj.rotation_euler = (center - obj.location).to_track_quat('-Z', 'Y').to_euler()
    else:
        scene.view_settings.view_transform = 'Standard'
        scene.world.color = (0.055, 0.055, 0.055)
        shading = scene.display.shading
        shading.light, shading.color_type = 'STUDIO', 'SINGLE'
        shading.single_color = (0.65, 0.65, 0.65)
        shading.show_shadows, shading.show_cavity, shading.cavity_type = True, True, 'BOTH'
        shading.background_type = 'WORLD'
        scene.display.render_aa = '16'
    return cameras, {'min': list(low), 'max': list(high), 'front_axis': spec['front_axis'],
                     'up_axis': '+Z', 'scale_semantics': 'Provider native units; human height not calibrated'}


def _require_background():
    if not bpy.app.background:
        raise ValueError('Candidate construction and reopen verification require an isolated background Blender')


def create_candidate(manifest, job_directory):
    from .contract import normalize_manifest
    _require_background()
    manifest = normalize_manifest(manifest)
    if manifest['operation'] != 'import_local':
        raise ValueError('Expected import_local operation')
    job = Path(job_directory).resolve()
    source = Path(manifest['input']['file'])
    if digest(source) != manifest['input']['expected_sha256']:
        raise ValueError('Input SHA changed')
    preflight_glb(source)
    candidate = job / 'output-candidate.blend'
    if candidate.exists():
        raise ValueError('Candidate destination already exists')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    imported = bpy.ops.import_scene.gltf(filepath=str(source), import_pack_images=True,
                                        import_shading='NORMALS', bone_heuristic='BLENDER',
                                        disable_bone_shape=True, import_scene_as_collection=False)
    if 'FINISHED' not in imported:
        raise ValueError('Native GLB import did not finish')
    scene = bpy.context.scene
    scene.name = 'ModelingCandidate'
    scene[OWNER] = json.dumps({'schema_version': '1.0', 'source_sha256': manifest['input']['expected_sha256'],
                               'provenance': manifest.get('provenance', {})}, ensure_ascii=False)
    before = snapshot(scene)
    if not before['meshes']:
        raise ValueError('No mesh objects imported')
    spec = manifest['preview']
    cameras, bounds = setup_preview(scene, spec) if spec['enabled'] else ({}, None)
    scene_name = scene.name
    bpy.ops.wm.save_as_mainfile(filepath=str(candidate), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(candidate), load_ui=False, use_scripts=False)
    after = snapshot()
    if before != after:
        write_json(job / 'output-reopen-mismatch.json', {'before': before, 'after': after})
        raise ValueError('Native save/reopen content mismatch')
    previews = []
    for view, name in cameras.items():
        scene = bpy.context.scene
        scene.camera = bpy.data.objects[name]
        path = job / ('output-preview-' + view + '.png')
        if path.exists():
            raise ValueError('Preview destination already exists')
        scene.render.filepath = str(path)
        if 'FINISHED' not in bpy.ops.render.render(write_still=True):
            raise ValueError('Preview render did not finish')
        previews.append({'view': view, 'file': str(path), 'sha256': digest(path),
                         'width': spec['width'], 'height': spec['height'], 'engine': spec['engine']})
    if digest(source) != manifest['input']['expected_sha256']:
        raise ValueError('Source changed during candidate construction')
    return {'schema_version': '1.0', 'producer': OWNER, 'version': VERSION,
            'operation': 'import_local', 'input': manifest['input'],
            'provenance': manifest.get('provenance', {}),
            'candidate': str(candidate), 'candidate_sha256': digest(candidate), 'scene_name': scene_name,
            'content': after, 'bounds': bounds, 'previews': previews,
            'save_reopen': 'pass', 'save_reopen_scope': 'same_process',
            'fresh_process_reopen': 'not_run', 'visual_review': 'pending',
            'blender': bpy.app.version_string, 'build': bpy.app.build_hash.decode(),
            'limitations': ['Native importer preservation checked after import, not full GLB semantic equivalence',
                            'No retopology, clothing completion, rigging or deformation quality claim',
                            'Semantic part count does not certify production separation',
                            'Material and geometry quality require actual image review']}


def verify_candidate(candidate_file, expected_content):
    _require_background()
    bpy.ops.wm.open_mainfile(filepath=str(candidate_file), load_ui=False, use_scripts=False)
    actual = snapshot()
    if actual != expected_content:
        raise ValueError('Independent candidate content differs from import report')
    return actual


def load_report(report_file, expected_sha256=None):
    path = Path(report_file).resolve(strict=True)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Candidate report exceeds 16 MiB')
    sha = digest(path)
    if expected_sha256 and sha != expected_sha256:
        raise ValueError('Report changed since review; reload it before adoption')
    report = json.loads(path.read_text(encoding='utf-8-sig'))
    if report.get('producer') != OWNER or report.get('operation') != 'import_local':
        raise ValueError('Not a Modeling Workflow candidate report')
    if report.get('save_reopen') != 'pass' or report.get('fresh_process_reopen') != 'pass':
        raise ValueError('Candidate requires successful independent reopen verification')
    candidate = Path(report['candidate'])
    if not candidate.is_absolute() or candidate.suffix.lower() != '.blend':
        raise ValueError('Report candidate must be an absolute blend path')
    if digest(candidate) != report['candidate_sha256']:
        raise ValueError('Candidate changed since verification')
    return report, sha


def adopt_candidate(report_file, expected_report_sha256):
    """Explicit additive operation: original scenes and datablocks stay intact."""
    if bpy.context.mode != 'OBJECT':
        raise ValueError('Switch to Object Mode before adopting a candidate')
    report, _ = load_report(report_file, expected_report_sha256)
    original_scene = bpy.context.scene
    with bpy.data.libraries.load(report['candidate'], link=False) as (source, destination):
        if report['scene_name'] not in source.scenes:
            raise ValueError('Candidate scene is missing')
        destination.scenes = [report['scene_name']]
    adopted = destination.scenes[0]
    if adopted is None:
        raise ValueError('Failed to append candidate scene')
    adopted['mw_adoption_report_sha256'] = expected_report_sha256
    adopted['mw_previous_scene'] = original_scene.name
    if bpy.context.window:
        bpy.context.window.scene = adopted
    return {'scene': adopted.name, 'previous_scene': original_scene.name,
            'source_saved': False, 'current_file_saved': False}
