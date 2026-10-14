"""IFC XML (.ifcxml) -> IFC text (.ifc), with IfcOpenShell, so the normal IFC readers can take over.
Runs in Blender's Python with the bundled wheels. Usage: ifcxml_to_ifc.py <in.ifcxml> <out.ifc>"""
import sys

import ifcopenshell

model = ifcopenshell.open(sys.argv[1])
model.write(sys.argv[2])
