# Hettich magnetic catch 5 kg: a parametric FreeCAD model written by hand in XML

[![build](https://github.com/Foadsf/hettich-magnetic-catch-fcstd/actions/workflows/build.yml/badge.svg)](https://github.com/Foadsf/hettich-magnetic-catch-fcstd/actions/workflows/build.yml)

A parametric FreeCAD 1.0 assembly of the Hettich DIY *Magnetschnäpper* 89261 / 89262 (5 kg magnetic catch with counterplate) and its 2D technical drawing. The whole model is **hand-written `Document.xml`**: no generator script, no FreeCAD macro, no cached geometry. FreeCAD rebuilds every solid, the assembly and the drawing from the XML when you open the file.

![Isometric view of the assembly](https://github.com/Foadsf/hettich-magnetic-catch-fcstd/releases/latest/download/Hettich_MagneticCatch_5kg_3D_iso.png)

![TechDraw sheet, A4, 2:1](https://github.com/Foadsf/hettich-magnetic-catch-fcstd/releases/latest/download/Hettich_MagneticCatch_5kg_Page.png)

## Downloads

Every file below is built by CI from `src/` and attached to the [latest release](https://github.com/Foadsf/hettich-magnetic-catch-fcstd/releases/latest). None of it is committed.

| File | What |
|---|---|
| [`Hettich_MagneticCatch_5kg.FCStd`](https://github.com/Foadsf/hettich-magnetic-catch-fcstd/releases/latest/download/Hettich_MagneticCatch_5kg.FCStd) | The FreeCAD document. Open it and press **Recompute** (Ctrl+R) once. |
| [`Hettich_MagneticCatch_5kg.step`](https://github.com/Foadsf/hettich-magnetic-catch-fcstd/releases/latest/download/Hettich_MagneticCatch_5kg.step) | STEP assembly: housing, magnet, two pole plates, counterplate |
| [`Hettich_MagneticCatch_5kg_Page.pdf`](https://github.com/Foadsf/hettich-magnetic-catch-fcstd/releases/latest/download/Hettich_MagneticCatch_5kg_Page.pdf) | The TechDraw sheet as PDF (also `.svg` and `.png`) |
| [`Hettich_MagneticCatch_5kg_Page.dxf`](https://github.com/Foadsf/hettich-magnetic-catch-fcstd/releases/latest/download/Hettich_MagneticCatch_5kg_Page.dxf) | The same sheet exported by TechDraw as DXF (views and dimensions) |
| `…_3D_iso.png`, `…_3D_front.png`, `…_3D_as_opened.png`, `…_Page_dxf.png` | Screenshots from the FreeCAD GUI and a render of the DXF |

## What is in the model

- **`Params` spreadsheet:** 21 driving dimensions plus 6 derived ones. Every solid, every placement and the counterplate's position in the assembly are expressions on it.
- **`Catch_89261` (App::Part):** the white plastic housing (flange, tower, two 3 × 6 adjustment slots, magnet pocket), the ferrite magnet, and two steel pole plates standing 1 mm proud of the face.
- **`Counterplate_89262` (App::Part):** a 38 × 11.8 × 1.2 stadium plate with two countersunk Ø4.5 holes at a 26.2 pitch. It is modelled in its own frame and mated onto the pole plates by its placement. `DoorGap` opens the "door".
- **`TPD_Sheet1` (TechDraw):** an A4 landscape sheet at 2:1, laid out like the manufacturer's sheet. It has the counterplate-over-housing view, the plan with slots, the end view, two counterplate views and an isometric, with 13 dimensions.

Change any value in `Params`, recompute, and the solids, the assembly and the drawing dimensions follow. CI checks this on every build.

### Parameters and their sources

The manufacturer's sheet is vector artwork whose labels are rounded to whole millimetres (26.2 is printed "26"). Features it does not dimension were measured from its line coordinates, scaled by the 45 mm dimension. The Hornbach pack print supplies the decimals.

| Alias | Value (mm) | Source |
|---|---|---|
| HsgLength / HsgDepth / HsgHeight | 45 / 14 / 13.3 | sheet 45, 14, "13" (measured 13.3) |
| FlangeThk | 4.3 | measured |
| TowerLength | 27.2 | pack print |
| SlotPitch / SlotWidth / SlotLength | 37 / 3 / 6 | sheet 37, 6; pack print 3 |
| SlotFromBack | 8 | measured |
| PoleLength / PoleThk / PoleProtrusion / MagnetHeight | 24.5 / 1.25 / 1 / 5 | measured |
| PlateLength / PlateWidth / PlateThk | 38 / 11.8 / 1.2 | sheet 38; pack print 11.8; thickness measured |
| HolePitch / HoleDia | 26.2 / 4.5 | pack print |
| PocketDepth / CskDia | 10 / 6.5 | assumed: hidden inside the part, or not dimensioned |
| DoorGap | 0 | assembly state: 0 means closed, with the counterplate seated on the pole plates |

## Repository layout

| Path | |
|---|---|
| `src/Document.xml` | **The source**: the hand-written parametric document |
| `src/GuiDocument.xml` | Hand-written view state: which solids are visible, colours, isometric camera |
| `src/A4_Landscape_Catch.svg` | Hand-written TechDraw template with editable title-block fields |
| `tools/fcstd_xml.py` | Lint, pack and verify loop for hand-written FreeCAD documents (Python stdlib; FreeCAD for the gates) |
| `docs/hand-writing-fcstd.md` | How to hand-write `Document.xml`, `GuiDocument.xml` and TechDraw: snippets and the traps behind them |
| `.github/workflows/build.yml` | Builds, verifies, exports, and publishes the release |

## Build and verify locally

You need FreeCAD 1.0.x. CI uses conda-forge's `freecad=1.0` (currently 1.0.0) on Ubuntu, and the same gates also pass on FreeCAD 1.0.2 for Windows. `fcstd_xml.py` finds FreeCAD via `FREECAD_DIR`, the default Windows install location, or `PATH`. Run each step on its own:

```sh
python tools/fcstd_xml.py lint src                      # counts, links, aliases, placements, GuiDocument traps
python tools/fcstd_xml.py pack src Hettich_MagneticCatch_5kg.FCStd
python tools/fcstd_xml.py verify Hettich_MagneticCatch_5kg.FCStd --edit DoorGap=5 --edit HoleDia=4 --step out/Hettich_MagneticCatch_5kg.step
python tools/fcstd_xml.py gui Hettich_MagneticCatch_5kg.FCStd --out out --edit HsgLength=50 --edit SlotPitch=40
python tools/fcstd_xml.py dxf out/Hettich_MagneticCatch_5kg_Page.dxf --png out/Hettich_MagneticCatch_5kg_Page_dxf.png   # pip install ezdxf matplotlib
python tools/fcstd_xml.py probe Hettich_MagneticCatch_5kg.FCStd --view ViewTop   # EdgeN/VertexN indices for new dimensions
python tools/fcstd_xml.py --selftest
```

What the gates check:

- **`verify`** (headless):
  - every object in the XML is instantiated;
  - one plain recompute rebuilds every shape;
  - the five parts are valid solids that do not interpenetrate, with the contacts listed;
  - each `--edit` moves the geometry, and restoring it returns identical numbers;
  - the STEP export reads back with the same solid count and volume.
- **`gui`** (a real display, Xvfb on CI):
  - the file opens with its parts visible;
  - the TechDraw page is opened and exported to DXF, PDF and SVG;
  - all 13 dimensions are read back;
  - each `--edit` moves the drawing's dimensions.

A new release is produced by pushing a tag: `git tag v1.0.1 && git push --tags`.

## Known limitations

- **No edge fillets:** the 0.5 mm edge radii on the manufacturer's sheet are omitted. Fillets reference topological edge names, which do not survive a rebuild from `Document.xml` alone.
- **Diameter label:** the diameter is written with the DXF control code `%%c`, so it reads **Ø4.5** in the DXF but shows literally as `%%c4.5` on the FreeCAD page. TechDraw's DXF writer puts UTF-8 bytes into a file declared `ANSI_1252`, so a real `⌀` would come out garbled.
- **No hidden lines** in the drawn views, because TechDraw's DXF writer emits hidden edges as solid lines.
- **No frame in the DXF:** it holds the views and dimensions only. TechDraw does not export the template frame or title block.
- **One fixed dimension:** the slot-length dimension hangs on two cosmetic vertices at fixed view coordinates, so it does not follow slot-parameter edits. The other 12 dimensions do.

## License and disclaimer

MIT, see [LICENSE](LICENSE). This is an independent model made from publicly available product information. It is not affiliated with or endorsed by Hettich; "Hettich" is a trademark of its owner. The manufacturer's drawing and the pack photo used as references are not redistributed here.
