#!/usr/bin/env python3
"""fcstd_xml.py - author, pack and verify HAND-WRITTEN FreeCAD documents.

The source of truth is a directory with a hand-written `Document.xml` (plus optional
`GuiDocument.xml` and any files its PropertyFileIncluded properties name, e.g. a TechDraw
template SVG). This tool closes the loop around it; it never writes the XML for you.

  fcstd_xml.py lint   <src_dir | Document.xml>            static checks, no FreeCAD needed
  fcstd_xml.py pack   <src_dir> <out.FCStd> [--force]     lint, then zip in FreeCAD's member order
  fcstd_xml.py verify <file.FCStd> [--parts A,B] [--edit Alias=value ...] [--json]
                      headless gates: every object instantiates, ONE plain recompute rebuilds
                      every shape, leaf solids valid, no interference, each --edit changes the
                      geometry and restoring it returns identical numbers
  fcstd_xml.py gui    <file.FCStd> --out DIR [--edit Alias=value ...] [--json]
                      VISIBLE FreeCAD window (the user's boundary): visibility/colour as opened,
                      3D screenshots, every TechDraw page opened then exported (DXF via
                      TechDraw.writeDXFPage, PDF, SVG), all dimensions read back, and with
                      --edit a check that the dimensions follow the spreadsheet
  fcstd_xml.py probe  <file.FCStd> [--view NAME] [--json] projected edges/vertices of TechDraw
                      views (offscreen is fine here) to pick EdgeN / VertexN references
  fcstd_xml.py dxf    <file.dxf> [--png out.png] [--json] numeric layout of a TechDraw DXF:
                      view extents, every dimension's value, line and text position, mojibake
  fcstd_xml.py --selftest

Why each check exists (all measured on FreeCAD 1.0.2, 2026-09-27, Hettich catch build; the
full list and XML snippets: docs/hand-writing-fcstd.md in https://github.com/Foadsf/hettich-magnetic-catch-fcstd):
  * Count attributes are loop bounds; a miscount silently misreads the rest of the object.
  * A spreadsheet alias equal to a unit symbol (L, T, N, ...) is rejected: "Invalid alias".
  * Without Touched="1" on the declaration a shape-less document opens empty and a plain
    recompute does nothing.
  * Placement stores a quaternion AND axis/angle; disagreeing forms reload wrong.
  * Without GuiDocument.xml every view provider starts hidden, and a hidden App::Part hides
    all its children. A HasExpansion attribute without an <Expand> block makes FreeCAD drop
    the whole view restore silently. GuiDocument.xml must come after the App-side members.
  * TechDraw computes nothing under freecadcmd; offscreen GUI exports have no template and
    every dimension at the origin. Judge drawings only from a visible GUI and from the DXF.
  * An App::Part as TechDraw Source also projects its hidden construction cutters.
  * A restored view does not inherit the page scale.
  * TechDraw's DXF writer emits UTF-8 into a file declared ANSI_1252 (use %%c, not the
    diameter glyph) and writes hidden lines as solid lines on the view layer.

Exit codes: 0 ok; 1 a gate failed; 2 bad input / FreeCAD not found.
Stdlib only, except `dxf`, which pulls ezdxf (+ matplotlib for --png) via fleet_bootstrap.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# FreeCAD unit symbols (and a few common abbreviations) that an alias must not equal.
UNIT_SYMBOLS = {
    "nm", "um", "mm", "cm", "dm", "m", "km", "mil", "in", "ft", "thou", "yd", "mi", "L", "l", "ml",
    "mg", "g", "kg", "t", "lb", "oz", "st", "cwt", "s", "min", "h", "A", "mA", "kA", "MA", "K",
    "mK", "uK", "mol", "cd", "N", "kN", "MN", "mN", "J", "kJ", "W", "kW", "V", "kV", "mV", "C",
    "F", "uF", "nF", "pF", "H", "T", "G", "Pa", "kPa", "MPa", "GPa", "bar", "psi", "ksi",
    "deg", "rad", "gon", "S", "Wb", "Ohm", "Hz", "eV", "Wh", "VA", "cal", "lbf", "mph", "sqft",
}
COUNTED = {  # element -> (count attribute, child tag)
    "Objects": ("Count", "Object"), "ObjectData": ("Count", "Object"),
    "ObjectDeps": ("Count", "Dep"), "LinkList": ("count", "Link"),
    "ExpressionEngine": ("count", "Expression"), "Cells": ("Count", "Cell"),
    "Map": ("count", "Item"), "Extensions": ("Count", "Extension"),
    "LinkSubList": ("count", "Link"), "CosmeticVertexList": ("count", "CosmeticVertex"),
    "ViewProviderData": ("Count", "ViewProvider"),
}
COMPUTED_PREFIXES = ("Part::", "PartDesign::", "Spreadsheet::Sheet", "TechDraw::DrawViewPart",
                     "TechDraw::DrawPage", "TechDraw::DrawViewDimension", "TechDraw::DrawProjGroup")
PART_CONTAINERS = ("App::Part",)


# ---------------------------------------------------------------- lint
def _src_paths(target: Path) -> tuple[Path, Path]:
    if target.is_dir():
        return target / "Document.xml", target
    return target, target.parent


def _quat_ok(pp: ET.Element) -> bool:
    try:
        q = [float(pp.get(k)) for k in ("Q0", "Q1", "Q2", "Q3")]
    except (TypeError, ValueError):
        return True  # no quaternion written; FreeCAD uses A/axis
    if pp.get("A") is None:
        return True
    a = float(pp.get("A"))
    ax = [float(pp.get(k, "0")) for k in ("Ox", "Oy", "Oz")]
    n = math.sqrt(sum(c * c for c in ax)) or 1.0
    ax = [c / n for c in ax]
    s = math.sin(a / 2)
    expect = [ax[0] * s, ax[1] * s, ax[2] * s, math.cos(a / 2)]
    nq = math.sqrt(sum(c * c for c in q)) or 1.0
    q = [c / nq for c in q]
    same = all(abs(x - y) < 1e-6 for x, y in zip(q, expect))
    neg = all(abs(x + y) < 1e-6 for x, y in zip(q, expect))
    return same or neg


def lint(target: Path) -> dict:
    doc_path, src = _src_paths(target)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        root = ET.parse(doc_path).getroot()
    except (OSError, ET.ParseError) as e:
        return {"errors": [f"cannot parse {doc_path}: {e}"], "warnings": [], "objects": 0}

    for el in root.iter():
        if el.tag == "Properties":
            props = [c for c in el if c.tag == "Property"]
            trans = [c for c in el if c.tag == "_Property"]
            if el.get("Count") is not None and int(el.get("Count")) != len(props):
                errors.append(f"<Properties> Count={el.get('Count')} but {len(props)} <Property> children")
            if int(el.get("TransientCount", "0")) != len(trans):
                errors.append(f"<Properties> TransientCount={el.get('TransientCount')} but {len(trans)} <_Property>")
            continue
        if el.tag in COUNTED:
            attr, child = COUNTED[el.tag]
            if el.get(attr) is None:
                continue
            kids = [c for c in el if c.tag == child]
            if int(el.get(attr)) != len(kids):
                errors.append(f"<{el.tag} {el.get('Name') or ''}> {attr}={el.get(attr)} but {len(kids)} <{child}>")

    objs = root.find("Objects")
    data = root.find("ObjectData")
    if objs is None or data is None:
        return {"errors": errors + ["missing <Objects> or <ObjectData>"], "warnings": warnings, "objects": 0}
    decl = {o.get("name"): o for o in objs.findall("Object")}
    datas = {o.get("name"): o for o in data.findall("Object")}
    for n in set(decl) | set(datas):
        if n not in decl or n not in datas:
            errors.append(f"object {n}: declared={n in decl} data={n in datas}")
    deps = {d.get("Name"): {x.get("Name") for x in d.findall("Dep")} for d in objs.findall("ObjectDeps")}
    if objs.get("Dependencies") == "1":
        for n in decl:
            if n not in deps:
                errors.append(f"object {n} has no <ObjectDeps> entry")
    types = {n: o.get("type", "") for n, o in decl.items()}

    aliases = {}
    for c in root.iter("Cell"):
        a = c.get("alias")
        if a:
            aliases[a] = c.get("content")
            if a in UNIT_SYMBOLS:
                errors.append(f"alias '{a}' equals a unit symbol -> FreeCAD rejects it ('Invalid alias')")
            if re.fullmatch(r"[A-Za-z]{1,2}\d+", a):
                errors.append(f"alias '{a}' looks like a cell address")

    brp = 0
    for n, o in datas.items():
        refs = set()
        for link in o.iter("Link"):
            v = link.get("value") or link.get("obj")
            if v:
                refs.add(v)
        for ex in o.iter("Expression"):
            for obj, attr in re.findall(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)", ex.get("expression", "")):
                if obj in decl:
                    refs.add(obj)
                    if types.get(obj, "").startswith("Spreadsheet") and attr not in aliases:
                        errors.append(f"{n}: expression uses unknown alias {obj}.{attr}")
        for r in refs:
            if r not in decl:
                errors.append(f"{n}: references undeclared object '{r}'")
        if objs.get("Dependencies") == "1":
            missing = refs - deps.get(n, set())
            if missing:
                errors.append(f"{n}: ObjectDeps lacks {sorted(missing)}")
        for pp in o.iter("PropertyPlacement"):
            if not _quat_ok(pp):
                errors.append(f"{n}: Placement quaternion and axis/angle disagree")
        for prop in o.iter("Property"):
            if prop.get("type") == "Part::PropertyPartShape":
                brp += 1
            if prop.get("name") == "FormatSpec":
                s = prop.find("String")
                if s is not None and any(ord(ch) > 127 for ch in s.get("value", "")):
                    warnings.append(f"{n}: FormatSpec has non-ASCII text; TechDraw's DXF writer garbles it (use %%c for diameter)")
        t = types.get(n, "")
        if t == "TechDraw::DrawViewPart" or t == "TechDraw::DrawProjGroupItem":
            names = {p.get("name") for p in o.iter("Property")}
            if "Scale" not in names:
                warnings.append(f"{n}: no Scale property; a restored view does not inherit the page scale")
            for p in o.iter("Property"):
                if p.get("name") == "Source":
                    for link in p.iter("Link"):
                        if types.get(link.get("value"), "") in PART_CONTAINERS:
                            warnings.append(f"{n}: Source is an App::Part; TechDraw also projects its hidden construction objects - list the finished solids")
    if brp:
        warnings.append(f"{brp} Part::PropertyPartShape properties: cached B-rep shipped; FreeCAD will trust it instead of rebuilding")
    else:
        untouched = [n for n, o in decl.items() if types[n].startswith(COMPUTED_PREFIXES) and o.get("Touched") != "1"]
        if untouched:
            warnings.append(f"{len(untouched)} computed objects lack Touched=\"1\" (e.g. {untouched[:3]}); the file opens empty and a plain recompute will not rebuild them")

    for fi in root.iter("FileIncluded"):
        if fi.get("file") and not (src / fi.get("file")).exists():
            errors.append(f"PropertyFileIncluded names '{fi.get('file')}' but {src / fi.get('file')} is missing")

    gui_path = src / "GuiDocument.xml"
    containers = [n for n, t in types.items() if t in PART_CONTAINERS]
    if not gui_path.exists():
        if any(t.startswith("Part::") for t in types.values()):
            errors.append("no GuiDocument.xml: in the FreeCAD GUI every object opens hidden")
    else:
        try:
            groot = ET.parse(gui_path).getroot()
        except ET.ParseError as e:
            errors.append(f"GuiDocument.xml does not parse: {e}")
            groot = None
        if groot is not None:
            if groot.get("HasExpansion") is not None and groot.find("Expand") is None:
                errors.append("GuiDocument.xml has a HasExpansion attribute but no <Expand> block: FreeCAD drops the WHOLE view restore")
            vps = {}
            for vp in groot.iter("ViewProvider"):
                vis = None
                for p in vp.iter("Property"):
                    if p.get("name") == "Visibility":
                        b = p.find("Bool")
                        vis = b is not None and b.get("value") == "true"
                vps[vp.get("name")] = vis
                if vp.get("name") not in decl:
                    errors.append(f"GuiDocument.xml: ViewProvider '{vp.get('name')}' names no object")
            for c in containers:
                if not vps.get(c):
                    errors.append(f"GuiDocument.xml: App::Part '{c}' is not set visible; a hidden container hides every child")
            if not any(v for n, v in vps.items() if types.get(n, "").startswith("Part::")):
                errors.append("GuiDocument.xml: no Part feature is set visible")
    return {"errors": errors, "warnings": warnings, "objects": len(decl), "aliases": len(aliases)}


# ---------------------------------------------------------------- pack
def pack(src: Path, out: Path, force: bool = False) -> dict:
    res = lint(src)
    if res["errors"] and not force:
        return {**res, "packed": False}
    doc = src / "Document.xml"
    members = [doc]
    for fi in ET.parse(doc).getroot().iter("FileIncluded"):
        f = src / fi.get("file")
        if f not in members:
            members.append(f)
    gui = src / "GuiDocument.xml"
    if gui.exists():
        members.append(gui)  # MUST follow the App-side members or FreeCAD skips it
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for m in members:
            z.write(m, m.name)
    return {**res, "packed": True, "out": str(out), "members": [m.name for m in members]}


# ---------------------------------------------------------------- FreeCAD runner
def find_freecad(gui: bool) -> str | None:
    names = ["freecad.exe", "FreeCAD.exe", "freecad", "FreeCAD"] if gui else \
            ["freecadcmd.exe", "FreeCADCmd.exe", "freecadcmd", "FreeCADCmd"]
    dirs = []
    if os.environ.get("FREECAD_DIR"):
        dirs.append(os.environ["FREECAD_DIR"])
    la = os.environ.get("LOCALAPPDATA", "")
    # Oldest supported first: a file built by a newer FreeCAD may not open in 1.0.
    dirs += sorted(glob.glob(os.path.join(la, "Programs", "FreeCAD 1.0*", "bin")))
    dirs += sorted(glob.glob(r"C:\Program Files\FreeCAD*\bin")) + sorted(glob.glob(os.path.join(la, "Programs", "FreeCAD*", "bin")))
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def run_in_freecad(helper: str, args: dict, gui: bool, offscreen: bool = False, timeout: int = 600) -> dict:
    exe = find_freecad(gui)
    if not exe:
        return {"fatal": "FreeCAD not found (set FREECAD_DIR to its bin directory)"}
    with tempfile.TemporaryDirectory() as td:
        hp = os.path.join(td, "fcx_helper.py")
        ap = os.path.join(td, "args.json")
        rp = os.path.join(td, "result.json")
        Path(hp).write_text(textwrap.dedent(HELPER_COMMON) + textwrap.dedent(helper), encoding="utf-8")
        args = {**args, "result": rp}
        Path(ap).write_text(json.dumps(args), encoding="utf-8")
        env = {**os.environ, "FCX_ARGS": ap}
        env.pop("QT_QPA_PLATFORM", None)
        if offscreen:
            env["QT_QPA_PLATFORM"] = "offscreen"
        try:
            proc = subprocess.run([exe, hp], env=env, capture_output=True, text=True, timeout=timeout)
            tail = (proc.stdout + proc.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            return {"fatal": f"FreeCAD timed out after {timeout}s (a modal dialog blocks the GUI?)"}
        if not os.path.exists(rp):
            return {"fatal": "helper produced no result", "log": tail}
        return json.loads(Path(rp).read_text(encoding="utf-8"))


HELPER_COMMON = r'''
import json, os, sys, time, traceback
import FreeCAD as App
ARGS = json.load(open(os.environ["FCX_ARGS"], encoding="utf-8"))
RES = {"fail": [], "info": []}

def done():
    with open(ARGS["result"], "w", encoding="utf-8") as f:
        json.dump(RES, f, indent=1, default=str)
    if App.GuiUp:
        os._exit(0)

def gshape(o):
    s = o.Shape.copy()
    s.Placement = o.getGlobalPlacement()
    return s

def leaves(doc):
    out = []
    for o in doc.Objects:
        if not o.TypeId.startswith(("Part::", "PartDesign::")) or not hasattr(o, "Shape"):
            continue
        if o.TypeId in ("PartDesign::Body",):
            continue
        consumers = [p for p in o.InList if p.TypeId.startswith(("Part::", "PartDesign::"))]
        if not consumers:
            out.append(o)
    return out

def sheet_of(doc, spec):
    if "." in spec.split("=")[0]:
        name, rest = spec.split(".", 1)
        return doc.getObject(name), rest
    for o in doc.Objects:
        if o.TypeId == "Spreadsheet::Sheet":
            return o, spec
    raise RuntimeError("no spreadsheet for edit " + spec)

def apply_edit(doc, spec):
    sh, rest = sheet_of(doc, spec)
    alias, val = rest.split("=", 1)
    cell = sh.getCellFromAlias(alias)
    if not cell:
        raise RuntimeError("alias not found: " + alias)
    old = sh.getContents(cell)
    sh.set(cell, val)
    return (sh, cell, old)
'''

HELPER_VERIFY = r'''
try:
    import zipfile, xml.etree.ElementTree as ET
    path = ARGS["file"]
    n_xml = len(ET.fromstring(zipfile.ZipFile(path).read("Document.xml")).find("Objects").findall("Object"))
    doc = App.openDocument(path)
    RES["objects_xml"], RES["objects_loaded"] = n_xml, len(doc.Objects)
    if n_xml != len(doc.Objects):
        RES["fail"].append("object count mismatch: some types could not be instantiated")
    RES["touched_on_open"] = sum(1 for o in doc.Objects if "Touched" in o.State)
    doc.recompute()   # ONE plain recompute, exactly what Ctrl+R does for the user
    null = [o.Name for o in doc.Objects if o.TypeId.startswith(("Part::", "PartDesign::")) and hasattr(o, "Shape") and o.Shape.isNull()]
    if null:
        RES["fail"].append("plain recompute left null shapes: " + ", ".join(null[:10]))
    bad = [o.Name for o in doc.Objects if not o.TypeId.startswith("TechDraw") and ("Invalid" in o.State or "Error" in o.State)]
    if bad:
        RES["fail"].append("objects in error: " + ", ".join(bad))
    names = ARGS.get("parts") or [o.Name for o in leaves(doc)]
    RES["parts"] = names

    def measure():
        m = {}
        for n in names:
            s = gshape(doc.getObject(n))
            bb = s.BoundBox
            m[n] = {"valid": s.isValid(), "solids": len(s.Solids), "volume": round(s.Volume, 4),
                    "bbox": [round(v, 4) for v in (bb.XMin, bb.XMax, bb.YMin, bb.YMax, bb.ZMin, bb.ZMax)]}
        return m

    base = measure()
    RES["base"] = base
    for n, m in base.items():
        if not m["valid"] or m["solids"] < 1:
            RES["fail"].append(f"{n}: invalid or no solid")
    shapes = {n: gshape(doc.getObject(n)) for n in names}
    inter, touch = [], []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            v = shapes[a].common(shapes[b]).Volume
            if v > 1e-6:
                inter.append(f"{a}/{b}={v:.4f}")
            elif shapes[a].distToShape(shapes[b])[0] < 1e-6:
                touch.append(f"{a}/{b}")
    RES["interference"], RES["contacts"] = inter, touch
    if inter:
        RES["fail"].append("interference: " + ", ".join(inter))
    if ARGS.get("step") and not RES["fail"]:
        # STEP of the verified assembly: one named solid per leaf part, in global position
        import Import, Part, re as _re
        tops = [o for o in doc.Objects if o.TypeId == "App::Part" and not any(p.TypeId == "App::Part" for p in o.InList)]
        top = tops[0].Label if tops else doc.Label + "_Assembly"
        tmp = App.newDocument(_re.sub(r"\W", "_", top))
        objs = []
        for n in names:
            src = doc.getObject(n)
            f = tmp.addObject("Part::Feature", n)
            f.Shape = gshape(src)
            f.Label = src.Label
            objs.append(f)
        tmp.recompute()
        Import.export(objs, ARGS["step"])
        back = Part.read(ARGS["step"])
        want = sum(base[n]["volume"] for n in names)
        RES["step"] = {"path": ARGS["step"], "solids": len(back.Solids), "volume": round(back.Volume, 4)}
        if len(back.Solids) != len(names) or abs(back.Volume - want) > 1e-3 * max(want, 1):
            RES["fail"].append(f"STEP round trip: {len(back.Solids)} solids / {back.Volume:.4f} vs {len(names)} / {want:.4f}")
        App.closeDocument(tmp.Name)
        App.setActiveDocument(doc.Name)
    for spec in ARGS.get("edits", []):
        sh, cell, old = apply_edit(doc, spec)
        doc.recompute()
        after = measure()
        changed = [n for n in names if after[n] != base[n]]
        RES["info"].append(f"edit {spec}: changed {changed}")
        if not changed:
            RES["fail"].append(f"edit {spec} changed no part (not driving geometry?)")
        sh.set(cell, old)
        doc.recompute()
        if measure() != base:
            RES["fail"].append(f"restoring {spec} did not return the original geometry")
except Exception:
    RES["fail"].append(traceback.format_exc())
done()
'''

HELPER_GUI = r'''
try:
    import FreeCADGui as Gui
    from PySide import QtGui
    app = QtGui.QApplication.instance()
    Gui.getMainWindow().resize(1600, 1000)

    def pump(sec):
        t = time.time()
        while time.time() - t < sec:
            app.processEvents(); time.sleep(0.05)

    out = ARGS["out"]
    doc = App.openDocument(ARGS["file"])
    base = os.path.splitext(os.path.basename(ARGS["file"]))[0]
    pump(1)
    RES["visible_on_open"] = [o.Name for o in doc.Objects if o.ViewObject and o.ViewObject.Visibility and o.TypeId.startswith(("Part::", "App::Part", "PartDesign::"))]
    if not RES["visible_on_open"]:
        RES["fail"].append("nothing is visible when the file opens (GuiDocument.xml missing, dropped, or hides the containers)")
    doc.recompute(); pump(2); doc.recompute(); pump(1)
    v = Gui.getDocument(doc.Name).activeView()
    shots = {}
    for tag, fn in (("as_opened", None), ("iso", "viewIsometric"), ("front", "viewFront")):
        if fn:
            getattr(v, fn)(); v.fitAll(); pump(0.5)
        p = os.path.join(out, f"{base}_3D_{tag}.png")
        v.saveImage(p, 1600, 1000, "White")
        shots[tag] = p
    RES["screenshots"] = shots
    pages = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"]
    RES["pages"] = []
    if pages:
        import TechDraw, TechDrawGui
        Gui.activateWorkbench("TechDrawWorkbench")
    for pg in pages:
        pg.ViewObject.show(); pump(3); doc.recompute(); pump(3)
        stem = os.path.join(out, f"{base}_{pg.Name}")
        TechDraw.writeDXFPage(pg, stem + ".dxf")
        TechDrawGui.exportPageAsPdf(pg, stem + ".pdf")
        TechDrawGui.exportPageAsSvg(pg, stem + ".svg")
        dims = []
        for d in pg.Views:
            if d.TypeId == "TechDraw::DrawViewDimension":
                try:
                    val = round(d.getRawValue(), 4)
                except Exception as e:
                    val = f"ERR {e}"
                    RES["fail"].append(f"{d.Name}: {e}")
                dims.append({"name": d.Name, "label": d.Label, "type": d.Type, "value": val, "X": d.X.Value, "Y": d.Y.Value})
        RES["pages"].append({"page": pg.Name, "dxf": stem + ".dxf", "pdf": stem + ".pdf", "svg": stem + ".svg", "dims": dims})
    if pages and ARGS.get("edits"):
        # Let TechDraw's hidden-line worker threads go idle FIRST. Measured 2026-09-27: without
        # this pause the front view (5 sources) never re-projected after the edit (its vertices
        # stayed at the old +/-22.5 while the housing was 50 wide), and touching the view did not
        # help; with a 10 s pause the same edit moved 45 -> 50 on two consecutive runs.
        pump(float(ARGS.get("idle", 10)))
    for spec in ARGS.get("edits", []):
        sh, cell, old = apply_edit(doc, spec)
        # TechDraw redoes hidden-line removal on a worker thread; a dimension recomputed before
        # that finishes keeps its OLD value and is not touched again. So touch the dimensions and
        # re-read until the values are stable (at least 5 rounds). Measured 2026-09-27: a fixed
        # 3 x 1.5 s wait passed once by luck and failed the next run.
        dims_all = [d for pg in pages for d in pg.Views if d.TypeId == "TechDraw::DrawViewDimension"]
        after, prev = {}, None
        for i in range(30):
            for d in dims_all:
                d.touch()
            doc.recompute(); pump(1.0)
            after = {d.Name: round(d.getRawValue(), 4) for d in dims_all}
            if i >= 4 and after == prev:
                break
            prev = after
        before = {d["name"]: d["value"] for p in RES["pages"] for d in p["dims"]}
        moved = {k: (before.get(k), v) for k, v in after.items() if before.get(k) != v}
        RES["info"].append(f"edit {spec}: dimensions changed {moved}")
        if pages and not moved:
            RES["fail"].append(f"edit {spec} changed no drawing dimension")
            diag = {"sheet_value": sh.getContents(cell)}
            for d in dims_all[:3]:
                refs = []
                for obj, subs in d.References2D:
                    for s in subs:
                        try:
                            g = obj.getVertexByIndex(int(s[6:])).Point if s.startswith("Vertex") else obj.getEdgeByIndex(int(s[4:])).BoundBox
                            refs.append(f"{obj.Name}.{s}={g}")
                        except Exception as e:
                            refs.append(f"{obj.Name}.{s}: {e}")
                diag[d.Name] = refs
            for o in leaves(doc):
                diag[o.Name] = str(gshape(o).BoundBox)
            RES["info"].append(f"diagnosis for {spec}: {diag}")
        sh.set(cell, old); doc.recompute(); pump(1)
except Exception:
    RES["fail"].append(traceback.format_exc())
done()
'''

HELPER_PROBE = r'''
try:
    from PySide import QtGui
    app = QtGui.QApplication.instance()
    doc = App.openDocument(ARGS["file"])
    views = [o for o in doc.Objects if o.TypeId in ("TechDraw::DrawViewPart", "TechDraw::DrawProjGroupItem")]
    if ARGS.get("view"):
        views = [v for v in views if v.Name == ARGS["view"]]
    for _ in range(60):
        doc.recompute(); app.processEvents(); time.sleep(0.1)
        if all(v.getVisibleEdges() for v in views):
            break
    RES["views"] = {}
    for v in views:
        edges, verts = [], []
        i = 0
        while True:
            try:
                e = v.getEdgeByIndex(i)
            except Exception:
                break
            c = e.Curve
            d = {"i": i, "type": type(c).__name__, "a": [round(e.Vertexes[0].Point.x, 3), round(e.Vertexes[0].Point.y, 3)],
                 "b": [round(e.Vertexes[-1].Point.x, 3), round(e.Vertexes[-1].Point.y, 3)]}
            if hasattr(c, "Radius"):
                d["r"] = round(c.Radius, 3); d["c"] = [round(c.Center.x, 3), round(c.Center.y, 3)]
            edges.append(d); i += 1
        i = 0
        while True:
            try:
                p = v.getVertexByIndex(i).Point
            except Exception:
                break
            verts.append({"i": i, "p": [round(p.x, 3), round(p.y, 3)]}); i += 1
        RES["views"][v.Name] = {"scale": v.Scale, "visible": len(v.getVisibleEdges()), "hidden": len(v.getHiddenEdges()),
                                "edges": edges, "vertices": verts,
                                "next_cosmetic_vertex": f"Vertex{len(verts)}"}
    RES["note"] = "coordinates are unscaled, relative to the view centre; cosmetic vertices are stored with Y inverted"
except Exception:
    RES["fail"].append(traceback.format_exc())
done()
'''


# ---------------------------------------------------------------- dxf
def dxf_report(path: Path, png: Path | None) -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    try:
        from fleet_bootstrap import ensure  # noqa: PLC0415  (fleet helper; absent in a standalone checkout)
        ensure("ezdxf", *(["matplotlib"] if png else []))
    except ImportError:
        pass  # standalone: pip install ezdxf matplotlib
    import ezdxf  # noqa: PLC0415
    from ezdxf import bbox  # noqa: PLC0415
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    res = {"codepage": doc.header.get("$DWGCODEPAGE"), "views": {}, "dims": [], "warnings": []}
    for lay in sorted({e.dxf.layer for e in msp}):
        ents = [e for e in msp if e.dxf.layer == lay and e.dxftype() != "DIMENSION"]
        if ents:
            b = bbox.extents(ents)
            res["views"][lay] = [round(b.extmin.x, 2), round(b.extmax.x, 2), round(b.extmin.y, 2), round(b.extmax.y, 2)]
    for e in msp.query("DIMENSION"):
        d = e.dxf
        text = d.get("text", "")
        res["dims"].append({"layer": d.layer, "text": text, "measured": round(e.get_measurement(), 4),
                            "line": [round(d.defpoint.x, 2), round(d.defpoint.y, 2)],
                            "text_at": [round(d.text_midpoint.x, 2), round(d.text_midpoint.y, 2)]})
        if re.search(r"[\u00c0-\u00ff][\u0080-\u20ff]", text):
            res["warnings"].append(f"{d.layer}: text '{text}' looks like UTF-8 read as {res['codepage']} (mojibake)")
    if png:
        from ezdxf.addons.drawing import matplotlib as mpl_draw  # noqa: PLC0415
        mpl_draw.qsave(msp, str(png), bg="#FFFFFF", dpi=150, size_inches=(11.69, 8.27))
        res["png"] = str(png)
    return res


# ---------------------------------------------------------------- selftest
SELFTEST_DOC = """<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="4" ProgramVersion="1.0R39319 (Git)" FileVersion="1" StringHasher="1">
    <StringHasher saveall="0" threshold="0" count="0"></StringHasher>
    <Properties Count="1" TransientCount="0">
        <Property name="Label" type="App::PropertyString"><String value="selftest"/></Property>
    </Properties>
    <Objects Count="4" Dependencies="1">
        <ObjectDeps Name="Params" Count="0"/>
        <ObjectDeps Name="Block" Count="1"><Dep Name="Params"/></ObjectDeps>
        <ObjectDeps Name="Bore" Count="1"><Dep Name="Params"/></ObjectDeps>
        <ObjectDeps Name="Part" Count="2"><Dep Name="Block"/><Dep Name="Bore"/></ObjectDeps>
        <Object type="Spreadsheet::Sheet" name="Params" id="1" Touched="1" />
        <Object type="Part::Box" name="Block" id="2" Touched="1" />
        <Object type="Part::Cylinder" name="Bore" id="3" Touched="1" />
        <Object type="Part::Cut" name="Part" id="4" Touched="1" />
    </Objects>
    <ObjectData Count="4">
        <Object name="Params">
            <Properties Count="1" TransientCount="0">
                <Property name="cells" type="Spreadsheet::PropertySheet">
                    <Cells Count="2" xlink="1"><XLinks count="0"></XLinks>
                        <Cell address="B1" content="20" alias="BlockLen" />
                        <Cell address="B2" content="3" alias="BoreRad" />
                    </Cells>
                </Property>
            </Properties>
        </Object>
        <Object name="Block">
            <Properties Count="2" TransientCount="0">
                <Property name="ExpressionEngine" type="App::PropertyExpressionEngine">
                    <ExpressionEngine count="1"><Expression path="Length" expression="Params.BlockLen"/></ExpressionEngine>
                </Property>
                <Property name="Visibility" type="App::PropertyBool"><Bool value="false"/></Property>
            </Properties>
        </Object>
        <Object name="Bore">
            <Properties Count="4" TransientCount="0">
                <Property name="ExpressionEngine" type="App::PropertyExpressionEngine">
                    <ExpressionEngine count="1"><Expression path="Radius" expression="Params.BoreRad"/></ExpressionEngine>
                </Property>
                <Property name="Height" type="App::PropertyLength"><Float value="20"/></Property>
                <Property name="Placement" type="App::PropertyPlacement"><PropertyPlacement Px="5" Py="5" Pz="-5" Q0="0" Q1="0" Q2="0" Q3="1" A="0" Ox="0" Oy="0" Oz="1"/></Property>
                <Property name="Visibility" type="App::PropertyBool"><Bool value="false"/></Property>
            </Properties>
        </Object>
        <Object name="Part">
            <Properties Count="3" TransientCount="0">
                <Property name="Base" type="App::PropertyLink"><Link value="Block"/></Property>
                <Property name="Tool" type="App::PropertyLink"><Link value="Bore"/></Property>
                <Property name="Visibility" type="App::PropertyBool"><Bool value="true"/></Property>
            </Properties>
        </Object>
    </ObjectData>
</Document>
"""
SELFTEST_GUI = """<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="1">
    <ViewProviderData Count="1">
        <ViewProvider name="Part">
            <Properties Count="1" TransientCount="0">
                <Property name="Visibility" type="App::PropertyBool"><Bool value="true"/></Property>
            </Properties>
        </ViewProvider>
    </ViewProviderData>
</Document>
"""


def selftest() -> int:
    ok = True

    def check(name, cond):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name)
        ok = ok and cond

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"
        src.mkdir()
        (src / "Document.xml").write_text(SELFTEST_DOC, encoding="utf-8")
        (src / "GuiDocument.xml").write_text(SELFTEST_GUI, encoding="utf-8")
        r = lint(src)
        check("good document lints clean", not r["errors"] and not r["warnings"])
        # negative controls: each must be caught
        bad = {
            "miscounted cells": SELFTEST_DOC.replace('<Cells Count="2"', '<Cells Count="3"'),
            "unit-symbol alias": SELFTEST_DOC.replace('alias="BlockLen"', 'alias="L"').replace("Params.BlockLen", "Params.L"),
            "unknown alias in expression": SELFTEST_DOC.replace("Params.BoreRad", "Params.BoreRadius"),
            "missing ObjectDeps link": SELFTEST_DOC.replace('<ObjectDeps Name="Part" Count="2"><Dep Name="Block"/><Dep Name="Bore"/>', '<ObjectDeps Name="Part" Count="1"><Dep Name="Block"/>'),
            "quaternion/axis disagree": SELFTEST_DOC.replace('Q0="0" Q1="0" Q2="0" Q3="1" A="0"', 'Q0="0.7071067811865476" Q1="0" Q2="0" Q3="0.7071067811865476" A="0"'),
        }
        for name, text in bad.items():
            (src / "Document.xml").write_text(text, encoding="utf-8")
            check(f"lint catches {name}", bool(lint(src)["errors"]))
        (src / "Document.xml").write_text(SELFTEST_DOC.replace(' Touched="1"', ""), encoding="utf-8")
        check("lint warns on missing Touched", any("Touched" in w for w in lint(src)["warnings"]))
        (src / "Document.xml").write_text(SELFTEST_DOC, encoding="utf-8")
        (src / "GuiDocument.xml").write_text(SELFTEST_GUI.replace('SchemaVersion="1"', 'SchemaVersion="1" HasExpansion="0"'), encoding="utf-8")
        check("lint catches HasExpansion without <Expand>", any("HasExpansion" in e for e in lint(src)["errors"]))
        (src / "GuiDocument.xml").write_text(SELFTEST_GUI, encoding="utf-8")
        out = Path(td) / "t.FCStd"
        p = pack(src, out)
        names = zipfile.ZipFile(out).namelist()
        check("pack writes Document.xml first and GuiDocument.xml last",
              p["packed"] and names[0] == "Document.xml" and names[-1] == "GuiDocument.xml")
        if find_freecad(False):
            v = run_in_freecad(HELPER_VERIFY, {"file": str(out), "edits": ["BlockLen=30"], "parts": []}, gui=False, timeout=300)
            check("verify passes the good document", not v.get("fatal") and not v["fail"])
            vol = v.get("base", {}).get("Part", {}).get("volume")
            check("verify volume = 20*10*10 - pi*3^2*10", vol is not None and abs(vol - (2000 - math.pi * 90)) < 1e-3)
            (src / "Document.xml").write_text(SELFTEST_DOC.replace("Params.BlockLen", "20").replace(
                '<ObjectDeps Name="Block" Count="1"><Dep Name="Params"/></ObjectDeps>', '<ObjectDeps Name="Block" Count="0"/>'), encoding="utf-8")
            pack(src, out, force=True)
            v2 = run_in_freecad(HELPER_VERIFY, {"file": str(out), "edits": ["BlockLen=30"], "parts": []}, gui=False, timeout=300)
            check("verify fails an edit that drives nothing", any("changed no part" in f for f in v2.get("fail", [])))
        else:
            print("SKIP FreeCAD gates (FreeCAD not found)")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ---------------------------------------------------------------- CLI
def _emit(res: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(res, indent=1, default=str))
    else:
        for k, v in res.items():
            if k in ("errors", "fail") and v:
                for x in v:
                    print("ERROR", x)
            elif k == "warnings" and v:
                for x in v:
                    print("WARN ", x)
            elif k == "info":
                for x in v:
                    print("INFO ", x)
            elif k not in ("errors", "fail", "warnings", "info"):
                print(f"{k}: {json.dumps(v, default=str) if isinstance(v, (dict, list)) else v}")
    if res.get("fatal"):
        return 2
    return 1 if (res.get("errors") or res.get("fail")) else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="See the module docstring (python fcstd_xml.py -h | more) and docs/hand-writing-fcstd.md in https://github.com/Foadsf/hettich-magnetic-catch-fcstd")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("lint"); p.add_argument("target"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("pack"); p.add_argument("src"); p.add_argument("out"); p.add_argument("--force", action="store_true"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("verify"); p.add_argument("file"); p.add_argument("--parts", default=""); p.add_argument("--step", help="export the verified leaf solids to this STEP file")
    p.add_argument("--edit", action="append", default=[]); p.add_argument("--json", action="store_true"); p.add_argument("--timeout", type=int, default=600)
    p = sub.add_parser("gui"); p.add_argument("file"); p.add_argument("--out", required=True)
    p.add_argument("--edit", action="append", default=[]); p.add_argument("--json", action="store_true"); p.add_argument("--timeout", type=int, default=600)
    p = sub.add_parser("probe"); p.add_argument("file"); p.add_argument("--view"); p.add_argument("--json", action="store_true"); p.add_argument("--timeout", type=int, default=600)
    p = sub.add_parser("dxf"); p.add_argument("file"); p.add_argument("--png"); p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.cmd == "lint":
        return _emit(lint(Path(a.target)), a.json)
    if a.cmd == "pack":
        return _emit(pack(Path(a.src), Path(a.out), a.force), a.json)
    if a.cmd == "verify":
        parts = [x for x in a.parts.split(",") if x]
        step = os.path.abspath(a.step) if a.step else None
        return _emit(run_in_freecad(HELPER_VERIFY, {"file": os.path.abspath(a.file), "parts": parts, "edits": a.edit, "step": step}, gui=False, timeout=a.timeout), a.json)
    if a.cmd == "gui":
        os.makedirs(a.out, exist_ok=True)
        return _emit(run_in_freecad(HELPER_GUI, {"file": os.path.abspath(a.file), "out": os.path.abspath(a.out), "edits": a.edit}, gui=True, timeout=a.timeout), a.json)
    if a.cmd == "probe":
        res = run_in_freecad(HELPER_PROBE, {"file": os.path.abspath(a.file), "view": a.view}, gui=True, offscreen=True, timeout=a.timeout)
        if not a.json and "views" in res:
            for name, v in res["views"].items():
                print(f"== {name} scale={v['scale']} visible={v['visible']} hidden={v['hidden']} next cosmetic vertex={v['next_cosmetic_vertex']}")
                for e in v["edges"]:
                    print(f"  Edge{e['i']} {e['type']} {e['a']}->{e['b']}" + (f" r={e['r']} c={e['c']}" if "r" in e else ""))
                for x in v["vertices"]:
                    print(f"  Vertex{x['i']} {x['p']}")
            return 1 if res["fail"] else 0
        return _emit(res, a.json)
    if a.cmd == "dxf":
        return _emit(dxf_report(Path(a.file), Path(a.png) if a.png else None), a.json)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
