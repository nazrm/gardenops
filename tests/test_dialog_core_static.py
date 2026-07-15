from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shared_modal_primitives_stack_and_restore_focus() -> None:
    source = (ROOT / "frontend/src/components/dialogCore.ts").read_text(encoding="utf-8")
    quick_actions = (ROOT / "frontend/src/features/quickActionsFeature.ts").read_text(
        encoding="utf-8"
    )
    completion = (ROOT / "frontend/src/features/taskCompletionFlow.ts").read_text(encoding="utf-8")
    snooze = (ROOT / "frontend/src/features/taskSnoozeFlow.ts").read_text(encoding="utf-8")

    assert "const modalStack: ModalStackEntry[] = [];" in source
    assert "export interface ModalOptions" in source
    assert "function pushModal(dialog: HTMLElement, modalParent?: HTMLElement | null)" in source
    assert "const parent = modalParent ?? activeModal()?.dialog ?? null;" in source
    assert "function popModal(entry: ModalStackEntry)" in source
    assert 'parent.setAttribute("aria-hidden", "true")' in source
    assert 'parent.setAttribute("inert", "")' in source
    assert "entry.returnFocus.focus();" in source
    assert source.count("const modalEntry = pushModal(") == 2
    assert source.count("popModal(modalEntry);") == 2
    assert source.count("activeModal()?.dialog === dialog") == 2
    assert "modalParent?: HTMLElement | null | undefined;" in snooze
    assert "modalParent: options.modalParent" in completion
    assert quick_actions.count("modalParent: quickActionSheet()") == 2


def test_attention_browser_check_exercises_generic_modal_cancel_and_submit() -> None:
    source = (ROOT / "scripts/check_attention_today_e2e.cjs").read_text(encoding="utf-8")

    assert "async function exerciseGenericModalStack(page)" in source
    assert 'await child.getByRole("button", { name: /^Cancel$/i }).click();' in source
    assert 'await childAfterSubmit.getByRole("button", { name: /^Apply$/i }).click();' in source
    assert "await exerciseGenericModalStack(page);" in source


def test_complete_journey_exercises_quick_actions_child_modal_stack() -> None:
    source = (ROOT / "scripts/e2e/journeys/dailyAttentionWork.cjs").read_text(encoding="utf-8")

    for marker in (
        "Quick Actions date-dialog parent restoration after cancel",
        "Quick Actions date-dialog parent restoration after submit",
        "Quick Actions completion-dialog parent restoration after cancel",
        "Quick Actions completion-dialog parent restoration after submit",
    ):
        assert marker in source
