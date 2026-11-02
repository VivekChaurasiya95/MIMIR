"""Action Proposal Card: graduated-autonomy approval UI (Phase 3).

Renders one :class:`mimir.planning.ActionPlan` (or its
registry :class:`~mimir.actions.ActionSpec`) as a review card: diff
preview, shell commands, matched principles, typed steps, and the
safety checklist, with Authorize & Execute vs Reject / Modify actions.

Graduated gating lives here and in
:meth:`desktop_app.state.AppState.authorize_proposal`: low-risk plans
enable Authorize & Execute immediately, while medium/high-risk plans
keep it disabled until every safety-checklist box is confirmed.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..presenters import risk_badge
from ._layout import actions_row, badge, card, overline
from ._qt import load_qt

__all__ = [
    "build_proposal_card",
    "proposal_checklist_ack",
    "set_proposal_busy",
]


def _field(source: Any, name: str, default: Any = "") -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _risk_text(source: Any) -> str:
    value = getattr(_field(source, "risk", "low"), "value",
                    _field(source, "risk", "low"))
    text = str(value or "low").strip().lower()
    return text if text in ("low", "medium", "high") else "low"


def proposal_checklist_ack(card_widget: Any) -> bool:
    """True when every checklist box on ``card_widget`` is confirmed."""
    try:
        boxes = list(getattr(card_widget, "checkboxes", None) or [])
    except Exception:
        return False
    if not boxes:
        return True
    try:
        return all(bool(box.isChecked()) for box in boxes)
    except Exception:
        return False


def set_proposal_busy(card_widget: Any, busy: bool) -> None:
    """Disable (or re-enable) a card's action buttons during work."""
    for name in ("authorize_btn", "modify_btn", "reject_btn"):
        try:
            button = getattr(card_widget, name, None)
            if button is not None:
                button.setEnabled(not busy)
        except Exception:
            pass
    if not busy:
        try:
            sync = getattr(card_widget, "_sync_authorize", None)
            if callable(sync):
                sync()
        except Exception:
            pass


def _mono_font() -> Any:
    _, _, QtGui, _ = load_qt()
    try:
        font = QtGui.QFont("Consolas", 10)
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        return font
    except Exception:
        return None


def build_proposal_card(
    plan: Any,
    *,
    action_id: str = "",
    on_authorize: Optional[Callable[[], None]] = None,
    on_reject: Optional[Callable[[], None]] = None,
    on_modify: Optional[Callable[[], None]] = None,
) -> Any:
    """Build the review card for one plan (or registry spec).

    ``plan`` may be an :class:`~mimir.planning.ActionPlan`, an
    :class:`~mimir.actions.ActionSpec`, or either one's ``to_dict()``.
    The returned widget carries ``authorize_btn``, ``modify_btn``,
    ``reject_btn``, ``checkboxes``, ``action_id``, and ``plan``
    attributes for hosts and tests.
    """
    _, _, _, QtWidgets = load_qt()
    box, lay = card()

    task = str(_field(plan, "task", "") or _field(plan, "reason", ""))
    operation = str(_field(plan, "operation", ""))
    target = str(_field(plan, "target", ""))
    foe = str(_field(plan, "file_or_element", ""))
    reason = str(_field(plan, "reason", ""))
    risk = _risk_text(plan)
    label, kind = risk_badge(risk)
    steps = list(_field(plan, "steps", ()) or ())
    commands = [str(c) for c in (_field(plan, "commands", ()) or ())]
    diff = str(_field(plan, "diff_preview", "") or "")
    checklist = [str(c) for c in
                 (_field(plan, "safety_checklist", ()) or ())]
    ids = [str(i) for i in
           (_field(plan, "matched_principle_ids", ()) or ())]
    names = [str(n) for n in
             (_field(plan, "matched_principle_names", ()) or ())]
    perms = [str(p) for p in
             (_field(plan, "required_permissions", ()) or ())]
    plan_id = str(_field(plan, "plan_id", ""))

    head = QtWidgets.QHBoxLayout()
    head.setSpacing(8)
    head.addWidget(overline("Action proposal"))
    head.addStretch(1)
    head.addWidget(badge(label, kind))
    lay.addLayout(head)

    title = QtWidgets.QLabel(task or "(no task)")
    title.setObjectName("h2")
    title.setWordWrap(True)
    lay.addWidget(title)

    where = foe or target
    meta_bits = [operation or "assist"]
    if where:
        meta_bits.append(f"target: {where}")
    if perms:
        meta_bits.append("requires: " + ", ".join(perms))
    bound = action_id or plan_id
    if bound:
        meta_bits.append(f"binds to {bound}")
    meta = QtWidgets.QLabel(" · ".join(meta_bits))
    meta.setObjectName("muted")
    meta.setWordWrap(True)
    lay.addWidget(meta)

    if reason and reason != task:
        why = QtWidgets.QLabel(reason)
        why.setObjectName("muted")
        why.setWordWrap(True)
        lay.addWidget(why)

    principles_row = QtWidgets.QHBoxLayout()
    principles_row.setSpacing(6)
    caption = QtWidgets.QLabel("Matched principles:")
    caption.setObjectName("muted")
    principles_row.addWidget(caption)
    if ids:
        for index, pid in enumerate(ids):
            tip = names[index] if index < len(names) else ""
            chip = badge(pid, "analyzed")
            if tip:
                chip.setToolTip(tip)
            principles_row.addWidget(chip)
    else:
        none = QtWidgets.QLabel("(none — generic plan)")
        none.setObjectName("muted")
        principles_row.addWidget(none)
    principles_row.addStretch(1)
    lay.addLayout(principles_row)

    if steps:
        lay.addWidget(overline(f"Plan steps ({len(steps)})"))
        for index, step in enumerate(steps, 1):
            if isinstance(step, dict):
                skind = str(step.get("kind", ""))
                stitle = str(step.get("title", ""))
                scommand = str(step.get("command", ""))
            else:
                skind = str(getattr(getattr(step, "kind", ""), "value",
                                    getattr(step, "kind", "")))
                stitle = str(getattr(step, "title", ""))
                scommand = str(getattr(step, "command", ""))
            line = QtWidgets.QLabel(f"{index}. [{skind}] {stitle}"
                                    + (f"  —  $ {scommand}" if scommand
                                       else ""))
            line.setObjectName("muted")
            line.setWordWrap(True)
            lay.addWidget(line)

    if commands:
        lay.addWidget(overline("Commands"))
        cmd_view = QtWidgets.QPlainTextEdit()
        cmd_view.setReadOnly(True)
        cmd_view.setPlainText("\n".join(f"$ {c}" for c in commands))
        mono = _mono_font()
        if mono is not None:
            cmd_view.setFont(mono)
        cmd_view.setMaximumHeight(22 + 20 * min(len(commands), 5))
        lay.addWidget(cmd_view)

    lay.addWidget(overline("Diff preview"))
    diff_view = QtWidgets.QPlainTextEdit()
    diff_view.setReadOnly(True)
    diff_view.setPlainText(diff)
    diff_view.setPlaceholderText("(no file changes planned — "
                                 "read-only/command plan)")
    mono = _mono_font()
    if mono is not None:
        diff_view.setFont(mono)
    diff_view.setMaximumHeight(150)
    diff_view.setMinimumHeight(64)
    lay.addWidget(diff_view)

    lay.addWidget(overline("Safety checklist"))
    boxes: list[Any] = []
    if checklist:
        for position, item in enumerate(checklist):
            check = QtWidgets.QCheckBox(item)
            check.setObjectName(f"proposal_check_{position}")
            lay.addWidget(check)
            boxes.append(check)
    else:
        empty = QtWidgets.QLabel("(none recorded)")
        empty.setObjectName("muted")
        lay.addWidget(empty)

    try:
        from mimir.planning import describe_autonomy

        note = describe_autonomy(risk)
    except Exception:
        note = "Review carefully before authorizing."
    autonomy = QtWidgets.QLabel(note)
    autonomy.setObjectName("muted")
    autonomy.setWordWrap(True)
    lay.addWidget(autonomy)

    authorize_btn = QtWidgets.QPushButton("Authorize & Execute")
    authorize_btn.setObjectName("generate_btn")
    authorize_btn.setToolTip(
        "Approve this action only and run it. Medium/high-risk "
        "plans need every checklist box confirmed first.")
    modify_btn = QtWidgets.QPushButton("Modify")
    modify_btn.setObjectName("soft")
    modify_btn.setToolTip("Load this task back into the prompt for "
                          "editing (the pending proposal is dropped).")
    reject_btn = QtWidgets.QPushButton("Reject")
    reject_btn.setObjectName("chip")
    reject_btn.setToolTip("Drop this proposal without running anything.")
    lay.addLayout(actions_row(authorize_btn, modify_btn, reject_btn))

    needs_ack = risk in ("medium", "high")

    def _sync_authorize() -> None:
        if not needs_ack:
            authorize_btn.setEnabled(True)
            return
        try:
            authorize_btn.setEnabled(
                all(b.isChecked() for b in boxes))
        except Exception:
            authorize_btn.setEnabled(False)

    for check in boxes:
        try:
            check.toggled.connect(lambda _on=False: _sync_authorize())
        except Exception:
            pass
    _sync_authorize()

    if callable(on_authorize):
        authorize_btn.clicked.connect(lambda _c=False: on_authorize())
    if callable(on_modify):
        modify_btn.clicked.connect(lambda _c=False: on_modify())
    if callable(on_reject):
        reject_btn.clicked.connect(lambda _c=False: on_reject())

    box.authorize_btn = authorize_btn
    box.modify_btn = modify_btn
    box.reject_btn = reject_btn
    box.checkboxes = boxes
    box.action_id = action_id
    box.plan = plan
    box.diff_view = diff_view
    box._sync_authorize = _sync_authorize
    return box
