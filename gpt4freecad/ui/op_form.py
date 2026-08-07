"""Schema-driven parameter form for a single CAD operation.

Renders one editor per field straight from ``cad.schema.OPERATIONS[op]`` so the
engineering timeline gets precise, structured controls (spin boxes, dropdowns,
vector triples) for every op without any per-op UI code. ``value()`` returns a
validated op dict.
"""

from __future__ import annotations

import json

from .qt import QtWidgets, guard_wheel
from ..cad import schema


def summarize(op: dict) -> str:
    """One-line description of an op for the step list."""
    label = op.get("name") or op.get("target") or ""
    parts = []
    for k, v in op.items():
        if k in ("op", "name"):
            continue
        parts.append(f"{k}={_short(v)}")
    return f"{op.get('op','?')}  {label}  " + "  ".join(parts)


def _short(v):
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, list):
        return "[" + ",".join(_short(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{…}"
    return str(v)


def _spin(value=0.0, decimals=4, lo=-1e7, hi=1e7):
    sb = QtWidgets.QDoubleSpinBox()
    sb.setRange(lo, hi)
    sb.setDecimals(decimals)
    sb.setValue(float(value) if value is not None else 0.0)
    return sb


class OpForm(QtWidgets.QWidget):
    def __init__(self, op_name, values=None, defined_names=None, parent=None):
        super().__init__(parent)
        self.op_name = op_name
        self.spec = schema.OPERATIONS[op_name]
        self.refs = set(self.spec.get("refs", []))
        self.enums = self.spec.get("enums", {})
        self.defined_names = list(defined_names or [])
        self.values = dict(values or {})
        self._fields = []  # (field, kind, optional, getter, include_cb)
        self._build()
        # This form lives inside the timeline's scroll area and is almost
        # entirely spin boxes - scrolling past it must not silently retype the
        # dimensions of the step being edited.
        guard_wheel(self)

    # ------------------------------------------------------------------ #
    def _build(self):
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(2)
        form.setVerticalSpacing(1)
        for field, kind in self.spec["required"].items():
            form.addRow(f"{field}:", self._row(field, kind, optional=False))
        for field, kind in self.spec["optional"].items():
            form.addRow(f"{field} (opt):", self._row(field, kind, optional=True))

    def _row(self, field, kind, optional):
        container = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(container)
        h.setContentsMargins(0, 0, 0, 0)
        include = None
        if optional:
            include = QtWidgets.QCheckBox()
            include.setChecked(field in self.values)
            h.addWidget(include)
        editor, getter = self._editor(field, kind)
        h.addWidget(editor, 1)
        if include is not None:
            editor.setEnabled(include.isChecked())
            include.toggled.connect(editor.setEnabled)
        self._fields.append((field, kind, optional, getter, include))
        return container

    def _editor(self, field, kind):
        cur = self.values.get(field)

        if kind == schema.STRLIST:  # e.g. fuse/common 'parts'
            w = QtWidgets.QLineEdit(",".join(cur) if isinstance(cur, list) else "")
            w.setPlaceholderText("comma-separated object names")
            return w, lambda: [s.strip() for s in w.text().split(",") if s.strip()]

        if kind == schema.STRING and field in self.refs:
            w = QtWidgets.QComboBox()
            w.setEditable(True)
            w.addItems(self.defined_names)
            if cur:
                w.setCurrentText(str(cur))
            return w, lambda: w.currentText().strip()

        if kind == schema.STRING:
            w = QtWidgets.QLineEdit(str(cur) if cur is not None else "")
            return w, lambda: w.text().strip()

        if kind == schema.NUMBER:
            w = _spin(cur)
            return w, lambda: w.value()

        if kind == schema.INT:
            w = QtWidgets.QSpinBox()
            w.setRange(1, 100000)
            w.setValue(int(cur) if cur else 1)
            return w, lambda: w.value()

        if kind == schema.BOOL:
            w = QtWidgets.QCheckBox()
            w.setChecked(bool(cur))
            return w, lambda: w.isChecked()

        if kind == schema.ENUM:
            w = QtWidgets.QComboBox()
            choices = self.enums.get(field, [])
            w.addItems(choices)
            if cur and str(cur).upper() in {c.upper() for c in choices}:
                w.setCurrentText(str(cur).upper())
            return w, lambda: w.currentText()

        if kind == schema.VEC3:
            cur = cur if isinstance(cur, (list, tuple)) and len(cur) == 3 else [0, 0, 0]
            box = QtWidgets.QWidget()
            hb = QtWidgets.QHBoxLayout(box)
            hb.setContentsMargins(0, 0, 0, 0)
            spins = []
            for v in cur:
                sb = _spin(v)
                hb.addWidget(sb)
                spins.append(sb)
            return box, lambda: [s.value() for s in spins]

        if kind == schema.INTLIST:
            w = QtWidgets.QLineEdit(",".join(str(i) for i in cur) if isinstance(cur, list) else "")
            w.setPlaceholderText("e.g. 1,3,5")
            return w, lambda: _parse_intlist(w.text())

        # PROFILE / PLACEMENT -> JSON text
        text = json.dumps(cur) if cur is not None else ""
        w = QtWidgets.QPlainTextEdit(text)
        w.setMaximumHeight(30)
        w.setPlaceholderText("JSON, e.g. [[0,0],[10,0],[10,10]]"
                             if kind == schema.PROFILE else
                             'JSON, e.g. {"pos":[0,0,0]}')
        return w, lambda: _parse_json(w.toPlainText())

    # ------------------------------------------------------------------ #
    def value(self) -> dict:
        """Build and validate the op dict. Raises schema.SchemaError on problems."""
        op = {"op": self.op_name}
        for field, kind, optional, getter, include in self._fields:
            if optional and (include is None or not include.isChecked()):
                continue
            op[field] = getter()
        schema.validate_op(op, self.defined_names)
        return op


def _parse_intlist(text):
    text = text.strip()
    if not text:
        return []
    try:
        return [int(s) for s in text.split(",") if s.strip()]
    except ValueError:
        return text  # invalid -> let schema validation report it


def _parse_json(text):
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text  # invalid -> schema validation reports it
