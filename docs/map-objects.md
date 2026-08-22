# Areas And Containers

GardenOps uses the map to show garden areas and the existing plot model to
track where plants actually grow. This keeps patios, terraces, and similar
surfaces useful without creating a second plant-location system.

## Model

- An **area** is a mapped surface such as a patio, terrace, greenhouse,
  balcony, or another named area. Areas organize the map and contain zero or
  more containers, but are not plant assignment targets themselves.
- A **container** is a pot, planter, raised bed, or other plantable place. A
  container is a canonical `plots` row, so it uses the same plant quantities,
  observations, tasks, journals, issues, harvests, calendar links, attention,
  and media relationships as an ordinary plot.
- A container may be inside one area or stand alone. The hierarchy stops at
  area -> container; containers cannot contain other containers.
- A container has one stable generated plot ID and one editable display name.
  The ID is an internal reference and is not shown in normal workflows.
- `plot_plants` remains the only current plant-placement table. Moving a plant
  keeps historical records on their original plot ID and changes only current
  placement.

The former `garden_map_object_units` records are legacy layout data. Runtime
creation and editing no longer use them. Legacy imports translate useful
units into canonical containers; new layout exports write canonical container
fields once.

## User Workflow

1. Open the map's **Areas & containers** panel and choose **Add area**.
2. Choose the area type, give it a name, and place it on the map. Geometry and
   appearance remain under **Edit layout** when needed.
3. Select an area and choose **Add container**, or use **Add standalone
   container** for a pot that is not in an area.
4. Choose Pot, Planter, Raised bed, or Other and give it a name. GardenOps
   places the container automatically and shows its plant count.
5. Use **Place plant** for a plant with no current home. Use **Move** on a
   specific current home to choose another ordinary plot or container.
6. The destination picker is searchable and groups ordinary plots, area
   containers, and standalone containers. A move can transfer part or all
   of a quantity and explains a destination merge before confirmation.

Place and Move work with buttons, touch, and keyboard. Dragging a plant is not
required. Successful changes refresh the source, destination, plant details,
and map counts and are announced to assistive technology.

## Permissions And Lifecycle

Garden members can inspect areas and containers. Editors and administrators can
create, rename, and reparent containers. Plant assignment and movement still
respect access to the plant and any ordinary source or destination plot. Only
administrators archive containers.

Removing an area unparents its containers. It never removes a container, plant,
assignment, or history. An occupied container cannot be archived. An empty
container is archived rather than destructively deleting plot-linked history;
archived containers disappear from active selectors but remain resolvable for
historical records.

Container creation, reparenting, archiving, and plant movement are online-only.

## Export And Import

Layout exports use schema version 2. Areas are exported as map objects, while
canonical containers are exported once in the plot data with the area's public
ID. Plant assignments are not duplicated in layout exports. Imports resolve
areas before container parents, preserve omitted containers, and translate
schema-version-1 nested units at the import boundary.

## Limits

The first canonical container release intentionally does not model recursive
location trees, soil or capacity, per-container irrigation, offline moves,
direct planting on a patio or terrace, or a general-purpose location graph.
