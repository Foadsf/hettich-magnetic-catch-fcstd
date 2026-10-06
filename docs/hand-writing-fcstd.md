# Hand-writing FreeCAD documents: Document.xml, GuiDocument.xml and TechDraw

An `.FCStd` file is a ZIP archive. Its `Document.xml` is the parametric source of truth: every object, its type, its driving properties and expressions. The `*.brp` members next to it are only cached geometry. So a FreeCAD model can be **written by hand as XML**, versioned as text, and rebuilt by FreeCAD on open.

The format is barely documented, and most mistakes fail **silently**: the file opens, but a shape is empty, a part is hidden, or a dimension shows a stale value. This guide records what it took to get the magnetic catch in this repository right in FreeCAD **1.0.2**. Every snippet is copied from `src/`, which passes all the gates in `tools/fcstd_xml.py`.

## Order of work

1. **Get the numbers from the source data, not by eye.**
   - A vendor PDF drawing is usually vector: read the line coordinates (for example PyMuPDF `page.get_drawings()`) and scale them by one known dimension. Vendor sheets round their labels: this one prints 26.2 as "26" and 13.3 as "13".
   - A photo of a drawing: apply the EXIF orientation first, then trace it with a vectorizer (potrace or vtracer) rather than estimating pixel positions.
   - Keep a table with a source for every value: printed, measured, or assumed.
2. **Pick constructions that survive a rebuild from XML.**
   - Use primitives (`Part::Box`, `Cylinder`, `Cone`, …) combined with `Part::Cut` / `Part::Fuse` chains. Sketch placements, `Part::Extrusion` of multi-wire sketches, lofts and fillets (topological edge names) do not rebuild reliably from `Document.xml` alone.
   - `Part::MultiFuse` is fine for **overlapping** tools, such as a slot's core plus its two end cylinders.
   - Put each physical part in its own `App::Part` with local geometry, and drive the Part's `Placement` from the spreadsheet to form the assembly.
3. **Write `Document.xml`, then `GuiDocument.xml`, then the TechDraw template SVG.** Run `python tools/fcstd_xml.py lint src` after every edit.
4. **`pack` then `verify`**, with one `--edit Alias=value` per parameter that must drive geometry.
5. **Build the TechDraw page.**
   - Add the views without dimensions.
   - `python tools/fcstd_xml.py probe file.FCStd` lists every projected `EdgeN` / `VertexN` with coordinates; choose the dimension references from that list.
   - Place the labels using the rule below.
   - Run `gui`, and look at the exported PDF and the DXF render yourself.
6. **Ship only what a gate checked.** Previews from Qt's offscreen mode are unusable; see the traps.

## Document.xml skeleton

```xml
<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="4" ProgramVersion="1.0R39319 (Git)" FileVersion="1" StringHasher="1">
    <StringHasher saveall="0" threshold="0" count="0"></StringHasher>
    <Properties Count="1" TransientCount="0">
        <Property name="Label" type="App::PropertyString" status="1"><String value="MyPart"/></Property>
    </Properties>
    <Objects Count="N" Dependencies="1">
        <ObjectDeps Name="Flange" Count="1"><Dep Name="Params"/></ObjectDeps>   <!-- every link AND every expression target -->
        <Object type="Part::Box" name="Flange" id="18" Touched="1" />             <!-- Touched="1": see traps -->
    </Objects>
    <ObjectData Count="N">
        <Object name="Flange"> <Properties Count="…" TransientCount="0"> … </Properties> </Object>
    </ObjectData>
</Document>
```

Declare every object once in `<Objects>` and give it one `<ObjectData>` entry. XML comments are allowed between objects and inside `<Properties>`. Properties you leave out keep their defaults.

### Values, links, placements, expressions

```xml
<Property name="Length" type="App::PropertyLength"><Float value="45"/></Property>
<Property name="Refine" type="App::PropertyBool"><Bool value="true"/></Property>
<Property name="Base" type="App::PropertyLink"><Link value="Flange"/></Property>
<Property name="Shapes" type="App::PropertyLinkList"><LinkList count="3"><Link value="A"/><Link value="B"/><Link value="C"/></LinkList></Property>
<Property name="Placement" type="App::PropertyPlacement"><PropertyPlacement Px="-13.1" Py="0" Pz="0" Q0="-0.7071067811865476" Q1="0" Q2="0" Q3="0.7071067811865476" A="1.5707963267948966" Ox="-1" Oy="0" Oz="0"/></Property>
<Property name="ExpressionEngine" type="App::PropertyExpressionEngine">
    <ExpressionEngine count="2">
        <Expression path="Length" expression="Params.HsgLength"/>
        <Expression path=".Placement.Base.x" expression="-Params.HsgLength / 2"/>   <!-- leading dot for placement paths -->
    </ExpressionEngine>
</Property>
```

The placement above is −90° about X, which turns a primitive's +Z axis onto +Y (used here for cylinders and cones along Y). `Q0..Q3` are x, y, z, w, and the axis-angle pair `A`, `Ox..Oz` must describe the same rotation.

### Spreadsheet parameters

```xml
<Property name="cells" type="Spreadsheet::PropertySheet">
  <Cells Count="4" xlink="1"><XLinks count="0"></XLinks>
    <Cell address="A2" content="&apos;HsgLength" />
    <Cell address="B2" content="45" alias="HsgLength" />
    <Cell address="A25" content="&apos;MagnetCenterZ" />
    <Cell address="B25" content="=FlangeThk + PoleThk + MagnetHeight / 2" alias="MagnetCenterZ" />
  </Cells></Property>
```

Put derived values in the sheet as formulas so that feature expressions stay simple. Plain unitless numbers avoid unit-mismatch errors.

### App::Part containers

Each `App::Part` needs an `App::Origin` plus three `App::Line` and three `App::Plane` datum objects. The Part lists its children in `Group` (the Origin is not in `Group`) and links the Origin through its `Origin` property. The Origin lists the six datums in `OriginFeatures`, and each datum has a `Role` string. The datum placements are fixed:

| datum | Q0 Q1 Q2 Q3 | A (rad) | axis |
|---|---|---|---|
| X_Axis, XY_Plane | 0 0 0 1 | 0 | 0 0 1 |
| Y_Axis, YZ_Plane | 0.5 0.5 0.5 0.5 | 2.0943951023931953 | 1 1 1 |
| Z_Axis | 0.5 −0.5 0.5 0.5 | 2.0943951023931953 | 1 −1 1 |
| XZ_Plane | 0.7071067811865475 0 0 0.7071067811865475 | 1.5707963267948966 | 1 0 0 |

## GuiDocument.xml is required, or nothing is visible

```xml
<?xml version='1.0' encoding='utf-8'?>
<Document SchemaVersion="1">                       <!-- NO HasExpansion attribute -->
    <ViewProviderData Count="2">
        <ViewProvider name="Catch" expanded="1"><Properties Count="1" TransientCount="0">
            <Property name="Visibility" type="App::PropertyBool"><Bool value="true"/></Property>
        </Properties></ViewProvider>
        <ViewProvider name="Housing" expanded="0"><Properties Count="2" TransientCount="0">
            <Property name="ShapeColor" type="App::PropertyColor"><PropertyColor value="4126535680"/></Property>
            <Property name="Visibility" type="App::PropertyBool"><Bool value="true"/></Property>
        </Properties></ViewProvider>
    </ViewProviderData>
    <Camera settings="OrthographicCamera {&#10;  viewportMapping ADJUST_CAMERA&#10;  position 57.735 -51.835 64.71&#10;  orientation 0.742906 0.307722 0.594473  1.217116&#10;  nearDistance 1&#10;  farDistance 200&#10;  aspectRatio 1&#10;  focalDistance 100&#10;  height 70&#10;&#10;}&#10;"/>
</Document>
```

- **Visibility:** list every `App::Part` as visible, plus the finished solids. Everything left out opens hidden, which is what you want for cutters and datums.
- **Colour:** `ShapeColor` is the packed value `(R<<24)|(G<<16)|(B<<8)`. FreeCAD 1.0 stores colour in a binary `ShapeAppearance` member, but it still converts this XML form on load.
- **Camera:** the one above is FreeCAD's standard isometric view.

## TechDraw in XML

```xml
<!-- Page -->     KeepUpdated true; ProjectionType 0 (first angle); Scale 2; Template link; Views = views AND dimensions
<!-- Template --> <Property name="PageResult" type="App::PropertyFileIncluded"><FileIncluded file="A4_Landscape_Catch.svg"/></Property>
<!-- View -->
<Property name="Direction" type="App::PropertyVector"><PropertyVector valueX="0" valueY="-1" valueZ="0"/></Property>   <!-- points to the viewer -->
<Property name="XDirection" type="App::PropertyVector"><PropertyVector valueX="1" valueY="0" valueZ="0"/></Property>  <!-- view up = Direction x XDirection -->
<Property name="Source" type="App::PropertyLinkList"><LinkList count="2"><Link value="Housing"/><Link value="Plate"/></LinkList></Property>
<Property name="Scale" type="App::PropertyFloatConstraint"><Float value="2"/></Property>
<Property name="ScaleType" type="App::PropertyEnumeration"><Integer value="0"/></Property>
<!-- Dimension -->
<Property name="Type" type="App::PropertyEnumeration"><Integer value="1"/></Property>        <!-- 1 DistanceX, 2 DistanceY, 5 Diameter -->
<Property name="MeasureType" type="App::PropertyEnumeration"><Integer value="1"/></Property> <!-- 1 Projected -->
<Property name="References2D" type="App::PropertyLinkSubList"><LinkSubList count="2"><Link obj="ViewFront" sub="Vertex0"/><Link obj="ViewFront" sub="Vertex2"/></LinkSubList></Property>
<Property name="FormatSpec" type="App::PropertyString"><String value="%.2w"/></Property>     <!-- diameter: %%c%.2w -->
<Property name="X" type="App::PropertyDistance"><Float value="0"/></Property>                <!-- label offset from the view centre, page mm -->
```

- **Enumerations** are stored as bare integer indices.
- **Template fields:** the SVG template's editable fields are `<text freecad:editable="Key">` elements.
- **Cosmetic vertices** (a `TechDraw::PropertyCosmeticVertexList` on the view) supply points the projection lacks, such as the extremes of a slot. They are stored unscaled, relative to the view centre, **with Y inverted**. Their `LinkGeom`, and the `VertexN` a dimension refers to, is the next index after the projected vertices.

**Label placement (measured).** Let L be the view centre plus (X, Y), in page mm.

- **Horizontal dimensions:** the DXF puts the text at L and the dimension line at L−5; the FreeCAD page puts the text at L−5 and the line at L−10.
- **Vertical dimensions:** the page puts the text at +5 and the line at +9.5.
- **Clearance:** a dimension above a view needs L ≥ top edge + 12; one below needs L ≤ bottom edge − 5.

## Traps: symptom → cause → fix

| Symptom | Cause | Fix |
|---|---|---|
| Object data misread, odd defaults | a `Count`/`count` differs from its children (they are loop bounds) | `fcstd_xml.py lint` |
| `Invalid alias` | the alias equals a unit symbol (`L`, `T`, `N`, `mm`, …) | use descriptive aliases |
| File opens empty; Recompute does nothing | no cached shape and nothing marked touched | add `Touched="1"` to each computed object's declaration |
| Rotated part in the wrong frame | quaternion and axis/angle disagree | write both from the same rotation |
| GUI shows nothing | no `GuiDocument.xml`: view providers start hidden, and a hidden `App::Part` hides its children | add `GuiDocument.xml` with the containers visible |
| `GuiDocument.xml` ignored (visibility, colour, camera) | `HasExpansion` attribute present without an `<Expand>` block | drop the attribute |
| `GuiDocument.xml` ignored | zip member order | `Document.xml`, then included files, then `GuiDocument.xml` |
| TechDraw views have no edges | running under `freecadcmd`: hidden-line removal needs the GUI event loop | run the GUI (Xvfb on CI) |
| Page export has no template and dimensions at the origin | exported with Qt's offscreen platform | use a real display (Xvfb) and open the page first |
| Drawing shows construction cutters | view `Source` is an `App::Part` | list the finished solids instead |
| Views at 1:1 on a 2:1 page | a restored view does not inherit the page scale | set `Scale` and `ScaleType 0` on every view |
| Slot length reads 3 instead of 6 | DistanceX/Y between two arcs measures centre to centre | add cosmetic vertices at the extremes |
| `âŒ€4.5` in the DXF | the DXF writer puts UTF-8 into a file declared `ANSI_1252` | `%%c` in FormatSpec (the FreeCAD page then shows `%%c`) |
| Hidden lines look like real edges in the DXF | the DXF writer emits hidden edges solid on the view layer | `HardHidden false` in views that go to DXF |
| The DXF opens zoomed far out | the DXF writer sets no `$EXTMIN`/`$EXTMAX` and leaves the `*Active` viewport at (0,0), height 1000 | `fcstd_xml.py gui` fits every export; `dxf --fit` for a stray file; `dxf` fails an unfitted one. In ezdxf, set the modelspace layout's extents as well as the header, because its save overwrites the header from the layout |
| After a parameter edit a view keeps its old projection | the edit arrived while hidden-line threads were busy | let the GUI idle about 10 s before editing (`gui` does this) |
