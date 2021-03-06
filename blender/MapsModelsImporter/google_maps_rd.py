# Copyright (c) 2019 Elie Michel
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# The Software is provided “as is”, without warranty of any kind, express or
# implied, including but not limited to the warranties of merchantability,
# fitness for a particular purpose and noninfringement. In no event shall
# the authors or copyright holders be liable for any claim, damages or other
# liability, whether in an action of contract, tort or otherwise, arising from,
# out of or in connection with the software or the use or other dealings in the
# Software.
#
# This file is part of MapsModelsImporter, a set of addons to import 3D models
# from Maps services

import sys
import pickle
import struct
import renderdoc as rd

from meshdata import MeshData, makeMeshData
from rdutils import CaptureWrapper

_, CAPTURE_FILE, FILEPREFIX = sys.argv[:3]

# Start chrome with "chrome.exe --disable-gpu-sandbox --gpu-startup-dialog --use-angle=gl"

def list_relevant_calls(drawcalls, _strategy=0):
    """List the drawcalls related to drawing the 3D meshes thank to a ad hoc heuristic
    It may different in RenderDoc UI and in Python module, for some reason
    """
    first_call = ""
    if _strategy == 0:
        first_call = "glClear(Color = <0.000000, 0.000000, 0.000000, 1.000000>, Depth = <1.000000>)"
    elif _strategy == 1:
        first_call = "glClear(Color = <0.000000, 0.000000, 0.000000, 1.000000>, Depth = <1.000000>, Stencil = <0x00>)"
    relevant_drawcalls = []
    is_relevant = False
    for draw in drawcalls:
        if is_relevant:
            if draw.name.startswith("glDrawArrays(4)"):
                break
            relevant_drawcalls.append(draw)
        if draw.name.startswith(first_call):
            is_relevant = True

    if not relevant_drawcalls:
        relevant_drawcalls = list_relevant_calls(drawcalls, _strategy=1)

    return relevant_drawcalls

""" alternate version, for RenderDoc UI (TODO)
def list_relevant_calls(drawcalls):
    it = iter(drawcalls)
    parentdraw = next(it)
    while not parentdraw.name.startswith("Colour Pass #2"):
        print(parentdraw.name)
        parentdraw = next(it)
    relevant_drawcalls = []
    for drawcallId in range(len(parentdraw.children)):
        draw = parentdraw.children[drawcallId]
        if draw.name.startswith("glDrawElements"):
            relevant_drawcalls.append(draw)
    return relevant_drawcalls
"""

def main(controller):
    drawcalls = controller.GetDrawcalls()
    relevant_drawcalls = list_relevant_calls(drawcalls)

    for drawcallId, draw in enumerate(relevant_drawcalls):
        print("Draw call: " + draw.name)
        if not draw.name.startswith("glDrawElements"):
            print("(Skipping)")
            continue

        controller.SetFrameEvent(draw.eventId, True)
        state = controller.GetPipelineState()

        ib = state.GetIBuffer()
        vbs = state.GetVBuffers()
        attrs = state.GetVertexInputs()
        meshes = [makeMeshData(attr, ib, vbs, draw) for attr in attrs]

        try:
            # Position
            m = meshes[0]
            m.fetchTriangle(controller)
            indices = m.fetchIndices(controller)
            with open("{}{:05d}-indices.bin".format(FILEPREFIX, drawcallId), 'wb') as file:
                pickle.dump(indices, file)
            unpacked = m.fetchData(controller)
            with open("{}{:05d}-positions.bin".format(FILEPREFIX, drawcallId), 'wb') as file:
                pickle.dump(unpacked, file)

            # UV
            m = meshes[1]
            m.fetchTriangle(controller)
            unpacked = m.fetchData(controller)
            with open("{}{:05d}-uv.bin".format(FILEPREFIX, drawcallId), 'wb') as file:
                pickle.dump(unpacked, file)
        except RuntimeError as err:
            print("(Skipping: {})".format(err))
            continue

        # Vertex Shader Constants
        shader = state.GetShader(rd.ShaderStage.Vertex)
        ep = state.GetShaderEntryPoint(rd.ShaderStage.Vertex)
        ref = state.GetShaderReflection(rd.ShaderStage.Vertex)
        constants = {}
        for cb in ref.constantBlocks:
            block = {}
            variables = controller.GetCBufferVariableContents(shader, ep, cb.bindPoint, rd.ResourceId.Null(), 0)
            for var in variables:
                val = 0
                if var.members:
                    val = []
                    for member in var.members:
                        memval = 0
                        if member.type == rd.VarType.Float:
                            memval = member.value.fv[:member.rows * member.columns]
                        elif member.type == rd.VarType.Int:
                            memval = member.value.iv[:member.rows * member.columns]
                        # ...
                        val.append(memval)
                else:
                    if var.type == rd.VarType.Float:
                        val = var.value.fv[:var.rows * var.columns]
                    elif var.type == rd.VarType.Int:
                        val = var.value.iv[:var.rows * var.columns]
                    # ...
                block[var.name] = val
            constants[cb.name] = block
        with open("{}{:05d}-constants.bin".format(FILEPREFIX, drawcallId), 'wb') as file:
            pickle.dump(constants, file)

        # Texture
        # dirty
        resources = state.GetReadOnlyResources(rd.ShaderStage.Fragment)
        rid = resources[0].resources[0].resourceId

        texsave = rd.TextureSave()
        texsave.resourceId = rid
        texsave.mip = 0
        texsave.slice.sliceIndex = 0
        texsave.alpha = rd.AlphaMapping.Preserve
        texsave.destType = rd.FileType.PNG
        controller.SaveTexture(texsave, "{}{:05d}-texture.png".format(FILEPREFIX, drawcallId))

if __name__ == "__main__":
    if 'pyrenderdoc' in globals():
        pyrenderdoc.Replay().BlockInvoke(main)
    else:
        print("Loading capture from {}...".format(CAPTURE_FILE))
        with CaptureWrapper(CAPTURE_FILE) as controller:
            main(controller)
    