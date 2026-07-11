import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDOOR_TAB = ROOT / "frontend" / "src" / "tabs" / "indoorTab.ts"


def _source() -> str:
    return INDOOR_TAB.read_text(encoding="utf-8")


def test_indoor_renderer_defaults_to_read_only() -> None:
    source = _source()

    assert "export interface IndoorRenderOptions" in source
    assert re.search(
        r"export function renderIndoorPlants\(\s*"
        r"container: HTMLElement,\s*"
        r"\{ canWrite = false \}: IndoorRenderOptions = \{\},",
        source,
    )


def test_indoor_mutation_affordances_require_write_access() -> None:
    source = _source()
    render_body = source.split("export function renderIndoorPlants", 1)[1].split(
        "function renderResultsArea", 1
    )[0]
    results_body = source.split("function renderResultsArea", 1)[1].split(
        "function showRoomLabelEditor", 1
    )[0]

    assert re.search(
        r"if \(canWrite\) \{.*?container\.appendChild\(addBtn\);",
        render_body,
        flags=re.DOTALL,
    )
    assert "if (!canWrite || !onAddPlant) return;" in render_body
    assert "renderPlantCard(plant, indoorPlotId, { canWrite })" in results_body
    assert re.search(
        r"if \(canWrite\) \{.*?showRoomLabelEditor\(",
        results_body,
        flags=re.DOTALL,
    )


def test_indoor_direct_mutations_keep_local_write_guards() -> None:
    source = _source()

    assert re.search(
        r"if \(editBtn\) \{\s*if \(!canWrite\) return;.*?onEditPlant",
        source,
        flags=re.DOTALL,
    )
    assert re.search(
        r"if \(removeBtn\) \{\s*if \(!canWrite\) return;.*?"
        r"await removePlantFromPlotApi",
        source,
        flags=re.DOTALL,
    )
    assert re.search(
        r"function showRoomLabelEditor\(.*?canWrite: boolean,\s*\): void \{\s*"
        r"if \(!canWrite\) return;",
        source,
        flags=re.DOTALL,
    )
    assert re.search(
        r'saveBtn\.addEventListener\("click", async \(\) => \{\s*'
        r"if \(!canWrite\) return;.*?await updatePlotPlant",
        source,
        flags=re.DOTALL,
    )
