# SPDX-FileCopyrightText: 2023-2026 Sebastian Schrand
#
# SPDX-License-Identifier: GPL-2.0-or-later


__author__ = "BlenderMax contributors; Sebastian Sille; Jens M. Plonka; Philippe Lagadec"
__version__ = "2.0.1"
__date__ = "16 Nov 2026"


import bpy
from bpy_extras.io_utils import (
    ImportHelper,
    orientation_helper,
    axis_conversion,
    poll_file_object_drop,
)
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    StringProperty,
    CollectionProperty,
)

if "bpy" in locals():
    import importlib
    if "import_max" in locals():
        importlib.reload(import_max)


@orientation_helper(axis_forward='Y', axis_up='Z')
class ImportBlenderMax(bpy.types.Operator, ImportHelper):
    """Import Autodesk MAX"""
    bl_idname = "import_scene.blender_max"
    bl_label = "Import BlenderMax (.max)"
    bl_description = "Import Autodesk 3ds Max scenes, materials, UVs, hierarchy, and transform animation"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".max"
    filter_glob: StringProperty(default="*.max", options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH')

    scale_objects: FloatProperty(
        name="Scale",
        description="Scale factor for all objects",
        min=0.0, max=10000.0,
        soft_min=0.0, soft_max=10000.0,
        default=1.0,
    )
    use_image_search: BoolProperty(
        name="Image Search",
        description="Search subdirectories for any associated images "
        "(Warning, may be slow)",
        default=True,
    )
    texture_directory: StringProperty(
        name="Texture Directory",
        description="Optional root directory for resolving external material textures",
        subtype='DIR_PATH',
        default="",
    )
    object_filter: EnumProperty(
        name="Object Filter", options={'ENUM_FLAG'},
        items=(('MATERIAL', "Material".rjust(12), "", 'MATERIAL_DATA', 0x1),
               ('UV', "UV Maps".rjust(11), "", 'UV_DATA', 0x2),
               ('PRIMITIVE', "Primitive".rjust(13), "", 'CUBE', 0x4),
               ('EMPTY', "Empty".rjust(11), "", 'EMPTY_AXIS', 0x8),
               ('ARMATURE', "Armature".rjust(11), "", 'ARMATURE_DATA', 0x10),
               ('CAMERA', "Camera".rjust(11), "", 'CAMERA_DATA', 0x20),
               ('LIGHT', "Light".rjust(10), "", 'LIGHT_DATA', 0x40),
               ),
        description="Object types to import",
        default={'MATERIAL', 'UV', 'PRIMITIVE', 'EMPTY', 'ARMATURE', 'CAMERA', 'LIGHT'},
    )
    use_collection: BoolProperty(
        name="Collection",
        description="Create a new collection",
        default=False,
    )
    use_apply_matrix: BoolProperty(
        name="Apply Matrix",
        description="Use matrix to transform the objects",
        default=True,
    )
    use_animation: BoolProperty(
        name="Transform Animation",
        description="Import supported Position/Rotation/Scale controller keyframes",
        default=True,
    )
    use_scene_fps: BoolProperty(
        name="Use Scene FPS",
        description="Read frame rate and active frame range from the MAX Config stream",
        default=True,
    )
    animation_sampling: EnumProperty(
        name="Sampling",
        description="Choose whether to preserve source key times or bake every frame",
        items=(
            ('BAKE', "Bake Every Frame", "Sample transforms every source frame for reliable quaternion playback"),
            ('KEYS', "Source Keys", "Import only source key times for smaller actions"),
        ),
        default='BAKE',
    )
    animation_fps: FloatProperty(
        name="Source FPS",
        description="Source scene frame rate used to convert 3ds Max ticks to Blender frames",
        min=1.0,
        max=240.0,
        default=30.0,
    )
    frame_offset: FloatProperty(
        name="Frame Offset",
        description="Offset added to imported animation frames",
        default=0.0,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        import_include(layout, self)
        import_transform(layout, self)
        import_animation(layout, self)

    def execute(self, context):
        from . import import_max
        keywords = self.as_keywords(ignore=("axis_forward", "axis_up", "filter_glob"))
        global_matrix = axis_conversion(from_forward=self.axis_forward, from_up=self.axis_up,).to_4x4()
        keywords["global_matrix"] = global_matrix

        return import_max.load(self, context, **keywords)

    def invoke(self, context, event):
        return ImportHelper.invoke(self, context, event)


def import_include(layout, operator):
    header, body = layout.panel("MAX_import_include", default_closed=False)
    header.label(text="Include")
    if body:
        layrow = layout.row(align=True)
        layrow.prop(operator, "use_image_search")
        layrow.label(text="", icon='OUTLINER_OB_IMAGE' if operator.use_image_search else 'IMAGE_DATA')
        layout.prop(operator, "texture_directory")
        layout.column().prop(operator, "object_filter")
        layrow = layout.row(align=True)
        layrow.prop(operator, "use_collection")
        layrow.label(text="", icon='OUTLINER_COLLECTION' if operator.use_collection else 'GROUP')


def import_transform(layout, operator):
    header, body = layout.panel("MAX_import_transform", default_closed=False)
    header.label(text="Transform")
    if body:
        layout.prop(operator, "scale_objects")
        layrow = layout.row(align=True)
        layrow.prop(operator, "use_apply_matrix")
        layrow.label(text="", icon='VIEW_ORTHO' if operator.use_apply_matrix else 'MESH_GRID')
        layout.prop(operator, "axis_forward")
        layout.prop(operator, "axis_up")


def import_animation(layout, operator):
    header, body = layout.panel("MAX_import_animation", default_closed=False)
    header.label(text="Animation")
    if body:
        body.prop(operator, "use_animation")
        column = body.column()
        column.enabled = operator.use_animation
        column.prop(operator, "animation_sampling")
        column.prop(operator, "use_scene_fps")
        fps_row = column.row()
        fps_row.enabled = not operator.use_scene_fps
        fps_row.prop(operator, "animation_fps")
        column.prop(operator, "frame_offset")


class IO_FH_max(bpy.types.FileHandler):
    bl_idname = "IO_FH_blender_max"
    bl_label = "BlenderMax"
    bl_import_operator = "import_scene.blender_max"
    bl_export_operator = "export_scene.max"
    bl_file_extensions = ".max"

    @classmethod
    def poll_drop(cls, context):
        return poll_file_object_drop(context)


def menu_func(self, context):
    self.layout.operator(ImportBlenderMax.bl_idname, text="BlenderMax / Autodesk MAX (.max)")


def register():
    bpy.utils.register_class(ImportBlenderMax)
    bpy.utils.register_class(IO_FH_max)
    bpy.types.TOPBAR_MT_file_import.append(menu_func)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func)
    bpy.utils.unregister_class(IO_FH_max)
    bpy.utils.unregister_class(ImportBlenderMax)


if __name__ == "__main__":
    register()
