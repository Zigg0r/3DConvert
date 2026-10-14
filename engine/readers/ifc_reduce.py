"""Reduces an IFC file as text, without building the model, so that IfcOpenShell only has to load what carries geometry.
Keeps every physical element with its placement, geometry, openings, materials and colours. Drops what has no shape
or is not a building part: properties, quantities, types, classification, groups and systems, grids, annotations,
spaces. Relationships (aggregation, containment) are trimmed to what remains.
Usage: ifc_reduce.py <in.ifc> <out.ifc>"""
import os
import re
import sys
import time
from array import array
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ifc_text import REF, entities  # noqa: E402

# objects with a GlobalId that are dropped; also dropped: every IFCREL*, *TYPE, *STYLE and IFCSTRUCTURAL*
DROP_TYPES = {"IFCPROPERTYSET", "IFCELEMENTQUANTITY", "IFCSPACE", "IFCOPENINGELEMENT", "IFCANNOTATION",
              "IFCGRID", "IFCVIRTUALELEMENT", "IFCGROUP", "IFCSYSTEM", "IFCZONE", "IFCDISTRIBUTIONSYSTEM",
              "IFCDISTRIBUTIONCIRCUIT", "IFCBUILDINGSYSTEM", "IFCTASK", "IFCPERMIT", "IFCCOSTITEM",
              "IFCCOSTSCHEDULE", "IFCWORKPLAN", "IFCWORKSCHEDULE", "IFCACTOR", "IFCOCCUPANT", "IFCPROCEDURE",
              "IFCEVENT", "IFCPROPERTYSETTEMPLATE", "IFCSIMPLEPROPERTYTEMPLATE", "IFCCOMPLEXPROPERTYTEMPLATE",
              "IFCDISTRIBUTIONPORT", "IFCASSET", "IFCINVENTORY", "IFCCONSTRUCTIONEQUIPMENTRESOURCE",
              "IFCLABORRESOURCE", "IFCSUBCONTRACTRESOURCE", "IFCCREWRESOURCE", "IFCPROJECTORDER"}
# (openings come back through their wall: IfcRelVoidsElement)

# relationships that point backwards: kept when their target stays
REL_KINDS = {"IFCRELCONTAINEDINSPATIALSTRUCTURE": "list",  # (related elements), structure
             "IFCRELAGGREGATES": "aggregate",              # whole, (parts)
             "IFCRELVOIDSELEMENT": "voids",                # building element, opening
             "IFCRELFILLSELEMENT": "fills",                # opening, window/door
             "IFCRELASSOCIATESMATERIAL": "list"}           # (objects), material
STYLE_TYPES = {"IFCSTYLEDITEM", "IFCMATERIALDEFINITIONREPRESENTATION"}

GUID = re.compile(rb"^#\d+\s*=\s*[A-Z0-9_]+\s*\(\s*'[0-9A-Za-z_$]{22}'")


def keep_type(name):
    return not (name.startswith("IFCREL") or name.endswith("TYPE") or name.endswith("STYLE")
                or name.startswith("IFCSTRUCTURAL") or name in DROP_TYPES)


def run(src, dst):
    t0 = time.time()
    # pass 1: type, GlobalId and references of every entity, in compact arrays
    type_ids, type_names = {}, []
    size = 0
    for eid, _, _ in entities(src):
        size = max(size, eid)
    size += 1
    type_code = array("H", [0]) * size
    has_guid = bytearray(size)
    ref_start = array("l", [0]) * (size + 1)
    refs = array("l")
    first_ref = array("l", [-1]) * size  # first reference (target of a styled item / material definition rep.)
    ids = array("l")
    for eid, kind, line in entities(src):
        code = type_ids.get(kind)
        if code is None:
            code = type_ids[kind] = len(type_names)
            type_names.append(kind.decode())
        type_code[eid] = code
        if GUID.match(line):
            has_guid[eid] = 1
        found = [int(x) for x in REF.findall(line[line.index(b"("):])]
        ids.append(eid)
        ref_start[eid] = len(refs)
        refs.extend(found)
        if found:
            first_ref[eid] = found[0]
    ref_end = array("l", [0]) * size  # end index of each entity's references: the start of the next one in file order
    for k, eid in enumerate(ids):
        ref_end[eid] = ref_start[ids[k + 1]] if k + 1 < len(ids) else len(refs)
    print("pass 1: %d entities, %d references [%.0fs]" % (len(ids), len(refs), time.time() - t0), flush=True)

    kept_codes = {c for c, name in enumerate(type_names) if keep_type(name)}
    rel_kind = {type_ids[name.encode()]: kind for name, kind in REL_KINDS.items() if name.encode() in type_ids}
    style_codes = {type_ids[name.encode()] for name in STYLE_TYPES if name.encode() in type_ids}
    keep = bytearray(size)

    def follow(stack):
        """Mark everything the entities on the stack refer to; never enter an object with a GlobalId that is not kept."""
        while stack:
            e = stack.pop()
            for k in range(ref_start[e], ref_end[e]):
                r = refs[k]
                if not keep[r] and not has_guid[r]:
                    keep[r] = 1
                    stack.append(r)

    roots = [e for e in ids if has_guid[e] and type_code[e] in kept_codes]
    for e in roots:
        keep[e] = 1
    follow(list(roots))
    # relationships and styles, until nothing is added any more
    rels = [e for e in ids if type_code[e] in rel_kind]
    styles = [e for e in ids if type_code[e] in style_codes]
    changed, rounds = True, 0
    while changed:
        changed = False
        rounds += 1
        added = []
        for e in rels:
            if keep[e]:
                continue
            kind = rel_kind[type_code[e]]
            objs = [refs[k] for k in range(ref_start[e], ref_end[e]) if has_guid[refs[k]]]
            if kind in ("aggregate", "voids"):  # the first object after the owner history is the host
                host, rest = (objs[0], objs[1:]) if objs else (None, [])
                if host is not None and keep[host]:
                    keep[e] = 1
                    added.append(e)
                    for r in rest:  # parts (aggregate) only if they stay themselves; openings (voids) always
                        if not keep[r] and type_code[r] not in rel_kind and (kind == "voids" or type_code[r] in kept_codes):
                            keep[r] = 1
                            added.append(r)
            elif any(keep[r] for r in objs):  # 'list' / 'fills': kept when at least one object stays
                keep[e] = 1
                added.append(e)
        for e in styles:
            if not keep[e] and first_ref[e] >= 0 and keep[first_ref[e]]:
                keep[e] = 1
                added.append(e)
        if added:
            changed = True
            follow(added)
    kept = sum(1 for e in ids if keep[e])
    print("marking: %d of %d stay (%d rounds) [%.0fs]" % (kept, len(ids), rounds, time.time() - t0), flush=True)

    # pass 2: write; in relationships, drop references to objects that were dropped
    def prune(m):
        return m.group(0) if keep[int(m.group(1))] else b""
    written = trimmed = 0
    with open(src, "rb") as f, open(dst, "wb") as g:
        for line in f:
            g.write(line)
            if line.strip() == b"DATA;":
                break
        for eid, _, line in entities(src):
            if not keep[eid]:
                continue
            if type_code[eid] in rel_kind:
                shorter = re.sub(rb"#(\d+)", prune, line)
                shorter = re.sub(rb",\s*(?=[,)])", b"", shorter)  # stray commas
                shorter = re.sub(rb"\(\s*,", b"(", shorter)
                if shorter != line:
                    trimmed += 1
                if b"()" in shorter:
                    continue  # empty list: the relationship goes
                line = shorter
            g.write(line)
            written += len(line)
        g.write(b"ENDSEC;\nEND-ISO-10303-21;\n")
    print("written: %.0f MB, %d relationships trimmed [%.0fs]" % (written / 1e6, trimmed, time.time() - t0))
    stay = Counter(type_names[type_code[e]] for e in ids if keep[e] and has_guid[e])
    print("objects with a GlobalId that stay:", dict(stay.most_common(25)))


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
