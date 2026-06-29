# Map Objects And Nested Layouts

GardenOps supports a layout-only map object layer for garden structures that
sit above the normal plot grid, such as patios, terraces, and custom surfaces.
These objects let a garden map show larger surfaces without changing the
existing plot model.

## Model

- Existing plots remain the canonical units for plants, care tasks, journal
  links, issues, harvests, and calendar work.
- Map objects are top-level visual objects owned by a garden. They have a type,
  name, rectangle or ellipse shape, grid footprint, color, z-index, and optional
  internal layout.
- Nested units live inside one map object. The first supported unit types are
  pots and planters. They are also layout-only in this slice.
- Nested units cannot belong to a different garden than their parent object.

## User Workflow

From the map layer panel, editors can create a patio, terrace, or custom object
from the current plot selection. If no plots are selected, GardenOps creates a
small default object at the top-left of the map.

Selecting the object opens editor controls for its name, rectangle or ellipse
shape, color, row, column, width, height, and optional internal layout. Editors
can also use the selected-object map handles to move or resize the object one
grid cell at a time. Viewers can see objects and nested layouts but cannot
create or edit them.

When internal layout is enabled, editors can add pots or planters inside that
layout. Clicking an existing nested unit deletes it after the normal UI
confirmation path. Custom objects may remain layout-only surfaces without nested
units.

Map object labels can be selected directly on the map. The overlay is visual
only, so ordinary plot clicks still work unless the user clicks the object
label or object controls.

Objects with existing nested units must keep their internal layout enabled until
those units are removed. GardenOps rejects updates that would leave nested units
attached to a layout-less object.

## Export And Import

Layout export includes `map_objects` with their nested `units`. Import restores
the map object layer together with the rest of the layout. If imported public
ids collide with objects outside the target garden, GardenOps regenerates those
ids during import instead of linking across gardens.

When an import or snapshot restore omits the `map_objects` key, GardenOps treats
that as a legacy payload and preserves existing map objects. An explicit
`"map_objects": []` clears the map-object layer. Imported map objects must fit
inside the garden grid, nested units must fit inside their parent internal
layout, and imports are limited to 200 map objects and 500 nested units total.

## Current Limits

- Nested units do not yet accept plant assignments, tasks, journals, issues, or
  harvest records.
- Nested unit movement is not exposed yet.
- Shrinking the garden grid is blocked while an existing map object would fall
  outside the new bounds.
- Non-rectangular grouping beyond rectangle and ellipse remains a future map
  design problem, not a plot-model rewrite.
