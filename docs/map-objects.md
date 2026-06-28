# Map Objects And Nested Layouts

GardenOps supports a layout-only map object layer for garden structures that
sit above the normal plot grid, such as patios and terraces. These objects let a
garden map show larger surfaces without changing the existing plot model.

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

From the map layer panel, editors can create a patio or terrace from the current
plot selection. If no plots are selected, GardenOps creates a small default
object at the top-left of the map.

Selecting the object opens its internal layout. Editors can add pots or planters
inside that layout. Clicking an existing nested unit deletes it after the normal
UI confirmation path. Viewers can see objects and nested layouts but cannot
create or delete them.

Map object labels can be selected directly on the map. The overlay is visual
only, so ordinary plot clicks still work unless the user clicks the object
label or object controls.

## Export And Import

Layout export includes `map_objects` with their nested `units`. Import restores
the map object layer together with the rest of the layout. If imported public
ids collide with objects outside the target garden, GardenOps regenerates those
ids during import instead of linking across gardens.

## Current Limits

- Nested units do not yet accept plant assignments, tasks, journals, issues, or
  harvest records.
- Custom editing for object dimensions, shape changes, colors, names, and unit
  movement is not exposed yet.
- Non-rectangular grouping beyond rectangle and ellipse remains a future map
  design problem, not a plot-model rewrite.
