from __future__ import annotations

import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import yaml

from src.common.analysis_paths import get_analysis_output_path
from src.common.plot_catalog import (
    MASTER_INSTANCE_PLOT_NAMES,
    RESULT_PLOT_LEVELS,
    get_result_plot_specs,
    parse_result_plots_to_do,
    serialize_result_plots_to_do,
)

DELETE_FIELD = object()
EXPERIMENT_COMPARISON_ROW_SPLIT_LABEL_TO_KEY = {
    "None": "",
    "Patients": "patient_number",
    "Care units": "care_unit_number",
    "Test": "test",
}
EXPERIMENT_COMPARISON_ROW_SPLIT_KEY_TO_LABEL = {
    value: key for key, value in EXPERIMENT_COMPARISON_ROW_SPLIT_LABEL_TO_KEY.items()
}


def dump_inline_yaml(value) -> str:
    dumped = yaml.safe_dump(
        value,
        default_flow_style=True,
        sort_keys=False,
        allow_unicode=False).strip()
    return dumped if dumped else "null"


class ConfigEditor(ttk.LabelFrame):
    def __init__(
            self,
            parent,
            project_root: Path,
            path_var: tk.StringVar,
            status_cb,
            on_configs_to_do_preview_cb=None,
            **kwargs):
        super().__init__(parent, text="Configuration Panel", **kwargs)

        self.project_root = project_root
        self.path_var = path_var
        self.status_cb = status_cb
        self.on_configs_to_do_preview_cb = on_configs_to_do_preview_cb

        self.config_data: dict = {}
        self.current_file: Path | None = None
        self.current_section_path = "root"
        self.section_var = tk.StringVar(value="root")
        self.field_specs: dict[str, dict] = {}
        self.form_content_width = 0
        self.matrix_group_names: list[str] = []
        self.matrix_added_groups: set[str] = set()
        self.matrix_removed_groups: set[str] = set()
        self.matrix_new_group_var = tk.StringVar(value="")
        self.matrix_remove_group_var = tk.StringVar(value="")
        self.matrix_test_selection_vars: dict[str, tk.BooleanVar] = {}
        self.matrix_test_selection_state: dict[str, bool] = {}

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Config file").grid(row=0, column=0, padx=(0, 6), sticky="w")
        ttk.Entry(top, textvariable=self.path_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(top, text="Browse", command=self._browse_file).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(top, text="Load", command=self.load_file).grid(row=0, column=3, padx=(6, 0))
        ttk.Button(top, text="Save", command=self.save_file).grid(row=0, column=4, padx=(6, 0))

        section_row = ttk.Frame(self)
        section_row.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        section_row.columnconfigure(1, weight=1)

        ttk.Label(section_row, text="Section").grid(row=0, column=0, padx=(0, 6), sticky="w")
        self.section_combo = ttk.Combobox(
            section_row,
            textvariable=self.section_var,
            state="readonly")
        self.section_combo.grid(row=0, column=1, sticky="ew")
        self.section_combo.bind("<<ComboboxSelected>>", self._on_section_change)
        ttk.Button(section_row, text="Apply Form -> YAML", command=self.apply_form).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(section_row, text="Apply YAML -> Form", command=self.apply_yaml_text).grid(row=0, column=3, padx=(6, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))

        form_tab = ttk.Frame(self.notebook)
        self.notebook.add(form_tab, text="Parameters")

        form_tab.rowconfigure(0, weight=1)
        form_tab.columnconfigure(0, weight=1)

        self.form_canvas = tk.Canvas(form_tab, highlightthickness=0)
        self.form_canvas.grid(row=0, column=0, sticky="nsew")
        self.form_scroll = ttk.Scrollbar(form_tab, orient="vertical", command=self.form_canvas.yview)
        self.form_scroll.grid(row=0, column=1, sticky="ns")
        self.form_xscroll = ttk.Scrollbar(form_tab, orient="horizontal", command=self.form_canvas.xview)
        self.form_xscroll.grid(row=1, column=0, sticky="ew")
        self.form_canvas.configure(yscrollcommand=self.form_scroll.set)
        self._bind_form_xscroll_to_outer()

        self.form_inner = ttk.Frame(self.form_canvas)
        self.form_window = self.form_canvas.create_window((0, 0), window=self.form_inner, anchor="nw")

        self.form_inner.bind("<Configure>", self._on_form_configure)
        self.form_canvas.bind("<Configure>", self._on_canvas_configure)

        raw_tab = ttk.Frame(self.notebook)
        self.notebook.add(raw_tab, text="Raw YAML")
        raw_tab.rowconfigure(0, weight=1)
        raw_tab.columnconfigure(0, weight=1)
        self.raw_text = ScrolledText(raw_tab, wrap="none")
        self.raw_text.grid(row=0, column=0, sticky="nsew")

    def _on_form_configure(self, _event=None):
        # Keep track of the effective width requested by the form content.
        self.form_content_width = max(self.form_content_width, self.form_inner.winfo_reqwidth() + 24)
        self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all"))

    def _on_canvas_configure(self, event=None):
        if event is None:
            return
        target_width = max(event.width, self.form_content_width)
        self.form_canvas.itemconfig(self.form_window, width=target_width)

    def _bind_form_xscroll_to_outer(self):
        self.form_xscroll.configure(command=self.form_canvas.xview)
        self.form_canvas.configure(xscrollcommand=self.form_xscroll.set)

    def _bind_form_xscroll_to_canvas(self, target_canvas: tk.Canvas):
        self.form_xscroll.configure(command=target_canvas.xview)
        target_canvas.configure(xscrollcommand=self.form_xscroll.set)
        self.form_canvas.configure(xscrollcommand=lambda *_args: None)

    def _browse_file(self):
        initial = self._resolve_path(self.path_var.get())
        selected = filedialog.askopenfilename(
            title="Select YAML config",
            initialdir=str(initial.parent if initial else self.project_root),
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")])
        if selected:
            self.path_var.set(self._path_to_string(Path(selected)))

    def _resolve_path(self, path_str: str) -> Path:
        path = Path(path_str).expanduser()
        if not path.is_absolute():
            path = self.project_root.joinpath(path)
        return path

    def _path_to_string(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.project_root))
        except ValueError:
            return str(path)

    def load_file(self) -> bool:
        path_str = self.path_var.get().strip()
        if not path_str:
            messagebox.showwarning("Missing path", "Choose a YAML configuration file first.")
            return False

        path = self._resolve_path(path_str)
        if not path.exists():
            messagebox.showerror("File not found", f"Cannot find file:\n{path}")
            return False

        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
        except Exception as exc:
            messagebox.showerror("YAML error", f"Failed to parse YAML:\n{exc}")
            return False

        if not isinstance(loaded, dict):
            messagebox.showerror("Unsupported format", "Top-level YAML content must be a mapping/object.")
            return False

        self.current_file = path
        self.config_data = loaded
        self._reset_matrix_state()
        self._refresh_sections()
        self._populate_raw_text()
        self._build_form()
        self.status_cb(f"Loaded config: {path.name}")
        return True

    def save_file(self, show_success=True) -> bool:
        path_str = self.path_var.get().strip()
        if not path_str:
            messagebox.showwarning("Missing path", "Choose a YAML configuration file first.")
            return False

        active_tab = self.notebook.tab(self.notebook.select(), "text")
        if active_tab == "Raw YAML":
            if not self.apply_yaml_text(show_success=False):
                return False
        else:
            if not self.apply_form(show_success=False):
                return False

        path = self._resolve_path(path_str)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(self.config_data, fh, sort_keys=False, allow_unicode=False)
        except Exception as exc:
            messagebox.showerror("Save error", f"Failed to save file:\n{exc}")
            return False

        self.current_file = path
        self.status_cb(f"Saved config: {path.name}")
        if show_success:
            messagebox.showinfo("Saved", f"Configuration saved to:\n{path}")
        return True

    def _refresh_sections(self):
        sections: list[str] = ["root"]
        if "base" in self.config_data and isinstance(self.config_data["base"], dict):
            sections.append("base")
        groups = self.config_data.get("groups")
        if isinstance(groups, dict):
            for group_name, group_obj in groups.items():
                if isinstance(group_obj, dict):
                    sections.append(f"groups.{group_name}")

        self.section_combo["values"] = sections
        if self.section_var.get() not in sections:
            self.section_var.set(sections[0])
        self.current_section_path = self.section_var.get()

    def _reset_matrix_state(self):
        self.matrix_group_names = []
        self.matrix_added_groups.clear()
        self.matrix_removed_groups.clear()
        self.matrix_new_group_var.set("")
        self.matrix_remove_group_var.set("")
        self.matrix_test_selection_vars = {}
        self.matrix_test_selection_state = {}
        self._notify_configs_to_do_preview()

    def _is_configs_to_do_special_mode(self, base: dict, groups: dict) -> bool:
        if not isinstance(base, dict) or not isinstance(groups, dict):
            return False
        return isinstance(base.get("configs_to_do"), list)

    def _initialize_matrix_test_selection_state(self, group_names: list[str], base_configs_to_do):
        if len(group_names) == 0:
            self.matrix_test_selection_state = {}
            return

        keep_existing = len(self.matrix_test_selection_state) > 0
        selected_from_base: set[str] = set()

        if isinstance(base_configs_to_do, list) and "all" not in base_configs_to_do:
            selected_from_base = {str(name) for name in base_configs_to_do if isinstance(name, str)}

        if keep_existing:
            updated_state: dict[str, bool] = {}
            for group_name in group_names:
                if group_name in self.matrix_test_selection_state:
                    updated_state[group_name] = bool(self.matrix_test_selection_state[group_name])
                elif isinstance(base_configs_to_do, list) and "all" in base_configs_to_do:
                    updated_state[group_name] = True
                else:
                    updated_state[group_name] = group_name in selected_from_base
            self.matrix_test_selection_state = updated_state
            return

        if not isinstance(base_configs_to_do, list) or "all" in base_configs_to_do:
            self.matrix_test_selection_state = {group_name: True for group_name in group_names}
            return

        self.matrix_test_selection_state = {
            group_name: (group_name in selected_from_base) for group_name in group_names}

    def _get_selected_test_groups(self, group_names: list[str]) -> list[str]:
        return [group_name for group_name in group_names if self.matrix_test_selection_state.get(group_name, False)]

    def _format_configs_to_do_preview(self, group_names: list[str]) -> str:
        if len(group_names) == 0:
            return "[all]"

        selected = self._get_selected_test_groups(group_names)
        if len(selected) == len(group_names):
            return "[all]"
        if len(selected) == 0:
            return "[]"
        return "[" + ", ".join(selected) + "]"

    def _get_effective_configs_to_do_value(self, group_names: list[str]) -> list[str]:
        if len(group_names) == 0:
            return ["all"]
        selected = self._get_selected_test_groups(group_names)
        if len(selected) == len(group_names):
            return ["all"]
        return selected

    def _notify_configs_to_do_preview(self):
        if self.on_configs_to_do_preview_cb is None:
            return

        root = self.config_data if isinstance(self.config_data, dict) else {}
        base = root.get("base") if isinstance(root, dict) else None
        groups = root.get("groups") if isinstance(root, dict) else None
        if not isinstance(base, dict) or not isinstance(groups, dict):
            self.on_configs_to_do_preview_cb("-")
            return
        if not isinstance(base.get("configs_to_do"), list):
            self.on_configs_to_do_preview_cb("-")
            return

        group_names = [name for name, value in groups.items() if isinstance(value, dict)]
        if len(self.matrix_group_names) > 0:
            group_names = [name for name in self.matrix_group_names if name in group_names]
            for name in self.matrix_group_names:
                if name not in group_names and name not in self.matrix_removed_groups:
                    group_names.append(name)
        preview = self._format_configs_to_do_preview(group_names)
        self.on_configs_to_do_preview_cb(preview)

    def _populate_raw_text(self):
        self.raw_text.delete("1.0", tk.END)
        dumped = yaml.safe_dump(self.config_data, sort_keys=False, allow_unicode=False)
        self.raw_text.insert(tk.END, dumped)

    def _on_section_change(self, _event=None):
        self.current_section_path = self.section_var.get()
        self._build_form()

    def _get_section_obj(self):
        if self.current_section_path == "root":
            return self.config_data

        parts = self.current_section_path.split(".")
        ref = self.config_data
        for part in parts:
            if not isinstance(ref, dict) or part not in ref:
                return None
            ref = ref[part]
        return ref

    def _flatten_section(self, obj, prefix="") -> list[tuple[str, object]]:
        rows: list[tuple[str, object]] = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(value, dict):
                    rows.extend(self._flatten_section(value, path))
                else:
                    rows.append((path, value))
        else:
            rows.append((prefix, obj))
        return rows

    def _set_value_by_path(self, section: dict, dotted_path: str, value):
        parts = dotted_path.split(".")
        ref = section
        for part in parts[:-1]:
            if part not in ref or not isinstance(ref[part], dict):
                ref[part] = {}
            ref = ref[part]
        ref[parts[-1]] = value

    def _get_value_by_path(self, section: dict, dotted_path: str) -> tuple[bool, object]:
        ref: object = section
        for part in dotted_path.split("."):
            if not isinstance(ref, dict) or part not in ref:
                return False, None
            ref = ref[part]
        return True, ref

    def _delete_value_by_path(self, section: dict, dotted_path: str):
        parts = dotted_path.split(".")
        ref = section
        parents: list[tuple[dict, str]] = []
        preserved_group_name: str | None = None
        if len(parts) >= 3 and parts[0] == "groups" and parts[1] in self.matrix_group_names:
            preserved_group_name = parts[1]

        for part in parts[:-1]:
            if not isinstance(ref, dict) or part not in ref or not isinstance(ref[part], dict):
                return
            parents.append((ref, part))
            ref = ref[part]

        if not isinstance(ref, dict):
            return

        ref.pop(parts[-1], None)

        for parent, key in reversed(parents):
            child = parent.get(key)
            if preserved_group_name is not None and key == preserved_group_name:
                break
            if isinstance(child, dict) and len(child) == 0:
                parent.pop(key, None)
            else:
                break

    def _is_group_field(self, path: str) -> bool:
        if self.current_section_path.startswith("groups."):
            return True
        parts = path.split(".")
        return len(parts) >= 3 and parts[0] == "groups"

    def _is_root_matrix_mode(self, section: dict) -> bool:
        if self.current_section_path != "root":
            return False
        base = section.get("base")
        groups = section.get("groups")
        if not isinstance(base, dict) or not isinstance(groups, dict):
            return False
        extra_root_keys = [key for key in section.keys() if key not in ("base", "groups")]
        return len(extra_root_keys) == 0

    def _format_form_text(self, value) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (list, dict)):
            return dump_inline_yaml(value)
        if value is None:
            return ""
        return str(value)

    def _snapshot_form_values(self) -> dict[str, object]:
        snapshot: dict[str, object] = {}
        for path, spec in self.field_specs.items():
            if spec["kind"] == "bool":
                snapshot[path] = bool(spec["var"].get())
            else:
                snapshot[path] = spec["var"].get()
        return snapshot

    def _restore_form_values(self, snapshot: dict[str, object]):
        for path, value in snapshot.items():
            spec = self.field_specs.get(path)
            if spec is None:
                continue
            try:
                spec["var"].set(value)
            except Exception:
                continue

    def _add_matrix_group(self):
        if self.current_section_path != "root":
            return

        group_name = self.matrix_new_group_var.get().strip()
        if group_name == "":
            messagebox.showwarning("Missing group name", "Insert a group name first.")
            return
        if "." in group_name:
            messagebox.showerror("Invalid group name", "Group name cannot contain '.'.")
            return
        if group_name in self.matrix_group_names:
            messagebox.showwarning("Duplicate group", f"Group '{group_name}' already exists.")
            return

        groups_obj = self.config_data.get("groups")
        if not isinstance(groups_obj, dict):
            groups_obj = {}
            self.config_data["groups"] = groups_obj

        snapshot = self._snapshot_form_values()
        all_current_selected = (
            len(self.matrix_group_names) == 0 or
            all(self.matrix_test_selection_state.get(name, False) for name in self.matrix_group_names))
        self.matrix_group_names.append(group_name)
        existed_group = isinstance(groups_obj.get(group_name), dict)
        if not existed_group:
            self.matrix_added_groups.add(group_name)
            groups_obj[group_name] = {}
        else:
            groups_obj.setdefault(group_name, {})
        self.matrix_test_selection_state[group_name] = all_current_selected
        self.matrix_removed_groups.discard(group_name)
        self.matrix_remove_group_var.set(group_name)
        self.matrix_new_group_var.set("")
        self._build_form()
        self._restore_form_values(snapshot)
        self._notify_configs_to_do_preview()
        self.status_cb(f"Added group column: {group_name}")

    def _remove_matrix_group(self):
        if self.current_section_path != "root":
            return

        group_name = self.matrix_remove_group_var.get().strip()
        if group_name == "":
            messagebox.showwarning("Missing group", "Select a group column to remove.")
            return
        if group_name == "base":
            messagebox.showerror("Protected group", "The 'base' column cannot be removed.")
            return
        if group_name not in self.matrix_group_names:
            messagebox.showwarning("Group not found", f"Group '{group_name}' is not visible.")
            return

        snapshot = self._snapshot_form_values()
        self.matrix_group_names = [name for name in self.matrix_group_names if name != group_name]
        self.matrix_removed_groups.add(group_name)
        self.matrix_added_groups.discard(group_name)
        self.matrix_test_selection_state.pop(group_name, None)
        self.matrix_remove_group_var.set(self.matrix_group_names[0] if len(self.matrix_group_names) > 0 else "")
        self._build_form()
        self._restore_form_values(snapshot)
        self._notify_configs_to_do_preview()
        self.status_cb(f"Marked group for removal: {group_name}")

    def _build_root_matrix_form(self, section: dict):
        base = section.get("base", {})
        groups = section.get("groups", {})

        if not isinstance(base, dict) or not isinstance(groups, dict):
            return

        special_configs_to_do_mode = self._is_configs_to_do_special_mode(base, groups)

        base_rows = self._flatten_section(base)
        if special_configs_to_do_mode:
            base_rows = [(path, value) for path, value in base_rows if path != "configs_to_do"]

        if len(base_rows) == 0:
            ttk.Label(
                self.form_inner,
                text="Base section has no editable scalar/list fields.",
                foreground="#666").grid(row=0, column=0, sticky="w", padx=8, pady=8)
            return

        self.form_inner.columnconfigure(0, weight=1, minsize=0)
        self.form_inner.rowconfigure(0, weight=1, minsize=0)

        container = ttk.Frame(self.form_inner)
        container.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        container.columnconfigure(0, weight=0, minsize=0)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(0, weight=0)
        container.rowconfigure(1, weight=1)

        top_controls = ttk.Frame(container)
        top_controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        top_controls.columnconfigure(1, weight=0)
        top_controls.columnconfigure(4, weight=0)
        top_controls.columnconfigure(6, weight=1)
        ttk.Label(top_controls, text="New group").grid(row=0, column=0, sticky="w", padx=(0, 6))
        add_entry = ttk.Entry(top_controls, textvariable=self.matrix_new_group_var, width=24)
        add_entry.grid(row=0, column=1, sticky="w")
        add_entry.bind("<Return>", lambda _e: self._add_matrix_group())
        ttk.Button(top_controls, text="Add group column", command=self._add_matrix_group).grid(
            row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Separator(top_controls, orient="vertical").grid(row=0, column=3, sticky="ns", padx=10)
        ttk.Label(top_controls, text="Remove group").grid(row=0, column=4, sticky="w", padx=(0, 6))
        remove_combo = ttk.Combobox(
            top_controls,
            textvariable=self.matrix_remove_group_var,
            state="readonly",
            width=24)
        remove_combo.grid(row=0, column=5, sticky="w")
        ttk.Button(top_controls, text="Remove column", command=self._remove_matrix_group).grid(
            row=0, column=6, sticky="w", padx=(6, 0))

        labels_frame = ttk.Frame(container)
        labels_frame.grid(row=1, column=0, sticky="nsw", padx=(0, 8))
        labels_frame.columnconfigure(0, weight=1)

        values_canvas = tk.Canvas(
            container,
            highlightthickness=0,
            borderwidth=0,
            background=self.form_canvas.cget("background"))
        values_canvas.grid(row=1, column=1, sticky="nsew")
        values_inner = ttk.Frame(values_canvas)
        values_window = values_canvas.create_window((0, 0), window=values_inner, anchor="nw")
        self._bind_form_xscroll_to_canvas(values_canvas)

        existing_group_names = [name for name, value in groups.items() if isinstance(value, dict)]
        if len(self.matrix_group_names) == 0:
            self.matrix_group_names = list(existing_group_names)
        else:
            for group_name in existing_group_names:
                if group_name not in self.matrix_group_names:
                    self.matrix_group_names.append(group_name)
            self.matrix_group_names = [
                group_name for group_name in self.matrix_group_names
                if (group_name in existing_group_names or group_name in self.matrix_added_groups)
                and group_name not in self.matrix_removed_groups]

        group_names = list(self.matrix_group_names)
        remove_combo["values"] = group_names
        if self.matrix_remove_group_var.get() not in group_names:
            self.matrix_remove_group_var.set(group_names[0] if len(group_names) > 0 else "")

        if special_configs_to_do_mode:
            self._initialize_matrix_test_selection_state(group_names, base.get("configs_to_do"))
        else:
            self.matrix_test_selection_state = {}
        self.matrix_test_selection_vars = {}

        header_font = ("TkDefaultFont", 9, "bold")
        value_col_width = 180
        header_height = 30
        row_height = 28

        ttk.Label(labels_frame, text="parameter", anchor="w", font=header_font).grid(
            row=0, column=0, sticky="nw", padx=(4, 6), pady=(4, 8))

        values_inner.columnconfigure(0, weight=0, minsize=value_col_width)
        ttk.Label(values_inner, text="base", anchor="w", font=header_font).grid(
            row=0, column=0, sticky="w", padx=(6, 6), pady=(4, 8))

        for col_idx, group_name in enumerate(group_names, start=1):
            values_inner.columnconfigure(col_idx, weight=0, minsize=value_col_width)
            ttk.Label(values_inner, text=group_name, anchor="w", font=header_font).grid(
                row=0, column=col_idx, sticky="w", padx=(6, 6), pady=(4, 8))

        # Keep row geometry aligned between left labels and right values.
        labels_frame.rowconfigure(0, minsize=header_height)
        values_inner.rowconfigure(0, minsize=header_height)

        data_start_row = 1
        if special_configs_to_do_mode:
            data_start_row = 2
            ttk.Label(labels_frame, text="run test", anchor="w").grid(
                row=1, column=0, sticky="nw", padx=(4, 6), pady=2)
            ttk.Label(values_inner, text="from checks", anchor="w").grid(
                row=1, column=0, sticky="w", padx=6, pady=2)
            labels_frame.rowconfigure(1, minsize=row_height)
            values_inner.rowconfigure(1, minsize=row_height)

            for col_idx, group_name in enumerate(group_names, start=1):
                checked = bool(self.matrix_test_selection_state.get(group_name, False))
                group_var = tk.BooleanVar(value=checked)
                self.matrix_test_selection_vars[group_name] = group_var

                def _toggle(name=group_name):
                    current = self.matrix_test_selection_vars.get(name)
                    if current is None:
                        return
                    self.matrix_test_selection_state[name] = bool(current.get())
                    self._notify_configs_to_do_preview()

                ttk.Checkbutton(values_inner, variable=group_var, command=_toggle).grid(
                    row=1, column=col_idx, sticky="w", padx=6, pady=2)

        for row_idx, (path, base_value) in enumerate(base_rows, start=data_start_row):
            ttk.Label(labels_frame, text=path, anchor="w").grid(
                row=row_idx,
                column=0,
                sticky="nw",
                padx=(4, 6),
                pady=2)

            base_var = tk.StringVar(value=self._format_form_text(base_value))
            ttk.Entry(values_inner, textvariable=base_var, width=18).grid(
                row=row_idx, column=0, sticky="ew", padx=6, pady=2)
            self.field_specs[f"base.{path}"] = {
                "kind": "text",
                "var": base_var,
                "type": type(base_value),
                "is_none": base_value is None}

            for col_idx, group_name in enumerate(group_names, start=1):
                group_obj = groups.get(group_name, {})
                if not isinstance(group_obj, dict):
                    continue

                exists, group_value = self._get_value_by_path(group_obj, path)
                shown = self._format_form_text(group_value) if exists else ""
                group_var = tk.StringVar(value=shown)
                ttk.Entry(values_inner, textvariable=group_var, width=18).grid(
                    row=row_idx, column=col_idx, sticky="ew", padx=6, pady=2)
                self.field_specs[f"groups.{group_name}.{path}"] = {
                    "kind": "text",
                    "var": group_var,
                    "type": type(base_value),
                    "is_none": base_value is None}

            labels_frame.rowconfigure(row_idx, minsize=row_height)
            values_inner.rowconfigure(row_idx, minsize=row_height)

        row_count = len(base_rows) + data_start_row
        layout_state = {"pending": False}

        def _sync_layout():
            for ridx in range(row_count):
                try:
                    bbox_values = values_inner.grid_bbox(0, ridx)
                    values_height = bbox_values[3] if len(bbox_values) == 4 else 0
                except Exception:
                    values_height = 0

                try:
                    bbox_labels = labels_frame.grid_bbox(0, ridx)
                    labels_height = bbox_labels[3] if len(bbox_labels) == 4 else 0
                except Exception:
                    labels_height = 0

                min_target = header_height if ridx == 0 else row_height
                target_height = max(values_height, labels_height, min_target)
                labels_frame.rowconfigure(ridx, minsize=target_height)
                values_inner.rowconfigure(ridx, minsize=target_height)

            req_width = values_inner.winfo_reqwidth()
            req_height = max(values_inner.winfo_reqheight(), labels_frame.winfo_reqheight())
            canvas_width = values_canvas.winfo_width()
            values_canvas.configure(height=req_height)
            values_canvas.itemconfig(values_window, width=max(canvas_width, req_width), height=req_height)
            values_canvas.configure(scrollregion=(0, 0, req_width, req_height))

        def _schedule_layout_sync():
            if layout_state["pending"]:
                return
            layout_state["pending"] = True

            def _run():
                layout_state["pending"] = False
                try:
                    _sync_layout()
                except tk.TclError:
                    return

            self.after_idle(_run)

        def _on_values_inner_configure(_event=None):
            _schedule_layout_sync()

        def _on_values_canvas_configure(_event=None):
            _schedule_layout_sync()

        values_inner.bind("<Configure>", _on_values_inner_configure)
        values_canvas.bind("<Configure>", _on_values_canvas_configure)
        _schedule_layout_sync()
        values_canvas.xview_moveto(0.0)
        self._notify_configs_to_do_preview()

    def _split_field_group(self, path: str) -> tuple[str, str]:
        parts = path.split(".")
        if len(parts) <= 1:
            return "general", path
        if parts[0] == "groups" and len(parts) >= 3:
            return f"groups.{parts[1]}", ".".join(parts[2:])
        return parts[0], ".".join(parts[1:])

    def _build_form(self):
        for child in self.form_inner.winfo_children():
            child.destroy()
        self.field_specs.clear()
        self.form_content_width = 0
        self._bind_form_xscroll_to_outer()

        cols, rows = self.form_inner.grid_size()
        for idx in range(max(cols, 16)):
            self.form_inner.columnconfigure(idx, weight=0, minsize=0)
        for idx in range(max(rows, 16)):
            self.form_inner.rowconfigure(idx, weight=0, minsize=0)

        section = self._get_section_obj()
        if not isinstance(section, dict):
            ttk.Label(
                self.form_inner,
                text="Selected section is not a mapping/dict.",
                foreground="#666").grid(row=0, column=0, sticky="w", padx=8, pady=8)
            self._notify_configs_to_do_preview()
            return

        if self._is_root_matrix_mode(section):
            self._build_root_matrix_form(section)
            self.form_content_width = 0
            self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all"))
            return

        flattened = self._flatten_section(section)
        if len(flattened) == 0:
            ttk.Label(
                self.form_inner,
                text="No editable scalar/list fields in this section.",
                foreground="#666").grid(row=0, column=0, sticky="w", padx=8, pady=8)
            self._notify_configs_to_do_preview()
            return

        groups: dict[str, list[tuple[str, str, object]]] = {}
        for path, value in flattened:
            group_name, field_label = self._split_field_group(path)
            groups.setdefault(group_name, []).append((path, field_label, value))

        group_frames: dict[str, ttk.LabelFrame] = {}
        column_width = 320
        for col_idx, group_name in enumerate(groups.keys()):
            self.form_inner.columnconfigure(col_idx, weight=0, minsize=column_width)
            frame = ttk.LabelFrame(self.form_inner, text=group_name)
            frame.grid(row=0, column=col_idx, sticky="nsew", padx=(8, 8), pady=(8, 8))
            frame.columnconfigure(0, weight=0)
            frame.columnconfigure(1, weight=1)
            group_frames[group_name] = frame

        field_width = 20
        for group_name, group_fields in groups.items():
            group_frame = group_frames[group_name]

            for row_idx, (path, label, value) in enumerate(group_fields):
                ttk.Label(group_frame, text=label, anchor="w").grid(
                    row=row_idx,
                    column=0,
                    sticky="w",
                    padx=(8, 12),
                    pady=3)

                field_type = type(value)

                if isinstance(value, bool):
                    var = tk.BooleanVar(value=value)
                    widget = ttk.Checkbutton(group_frame, variable=var)
                    widget.grid(row=row_idx, column=1, sticky="w", padx=(0, 8), pady=3)
                    self.field_specs[path] = {"kind": "bool", "var": var}

                else:
                    if isinstance(value, (list, dict)):
                        shown = dump_inline_yaml(value)
                        hint = "yaml"
                    elif value is None:
                        shown = ""
                        hint = "none"
                    else:
                        shown = str(value)
                        hint = "scalar"

                    var = tk.StringVar(value=shown)
                    widget = ttk.Entry(group_frame, textvariable=var, width=field_width)
                    widget.grid(row=row_idx, column=1, sticky="ew", padx=(0, 8), pady=3)
                    if hint == "yaml":
                        ttk.Label(group_frame, text="yaml", foreground="#888").grid(
                            row=row_idx, column=2, sticky="w", padx=(0, 8))

                    self.field_specs[path] = {
                        "kind": "text",
                        "var": var,
                        "type": field_type,
                        "is_none": value is None}

        # Recompute content width from real rendered widgets so horizontal
        # scrolling can always reach the rightmost column without clipping.
        self.form_inner.update_idletasks()
        self.form_content_width = self.form_inner.winfo_reqwidth() + 24
        canvas_width = self.form_canvas.winfo_width()
        self.form_canvas.itemconfig(self.form_window, width=max(canvas_width, self.form_content_width))
        self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all"))
        self._notify_configs_to_do_preview()

    def _parse_form_value(self, path: str, spec: dict):
        if spec["kind"] == "bool":
            return bool(spec["var"].get())

        text_value = spec["var"].get()
        original_type = spec["type"]

        if self._is_group_field(path) and text_value.strip() == "":
            return DELETE_FIELD

        if original_type is int:
            return int(text_value)
        if original_type is float:
            return float(text_value)
        if original_type is bool:
            parsed = yaml.safe_load(text_value)
            if isinstance(parsed, bool):
                return parsed
            raise ValueError(f"Field '{path}' expects a boolean (true/false).")
        if original_type is str:
            return text_value
        if original_type is list:
            parsed = yaml.safe_load(text_value)
            if parsed is None:
                return []
            if not isinstance(parsed, list):
                raise ValueError(f"Field '{path}' expects a YAML list.")
            return parsed
        if original_type is dict:
            parsed = yaml.safe_load(text_value)
            if parsed is None:
                return {}
            if not isinstance(parsed, dict):
                raise ValueError(f"Field '{path}' expects a YAML mapping/dict.")
            return parsed
        if spec.get("is_none", False):
            if text_value.strip() in ["", "null", "None", "~"]:
                return None
            return text_value

        parsed = yaml.safe_load(text_value)
        return parsed

    def apply_form(self, show_success=True) -> bool:
        section = self._get_section_obj()
        if not isinstance(section, dict):
            messagebox.showerror("Invalid section", "Selected section is not editable.")
            return False

        pending_removed_groups: list[str] = []
        forced_removed_any = False
        if self.current_section_path == "root" and isinstance(section.get("groups"), dict):
            groups_obj = section["groups"]
            for group_name in self.matrix_group_names:
                groups_obj.setdefault(group_name, {})
            pending_removed_groups = [name for name in self.matrix_removed_groups if name != "base"]

            base_obj = section.get("base")
            if isinstance(base_obj, dict) and self._is_configs_to_do_special_mode(base_obj, groups_obj):
                visible_group_names = list(self.matrix_group_names)
                configs_to_do_value = self._get_effective_configs_to_do_value(visible_group_names)
                if base_obj.get("configs_to_do") != configs_to_do_value:
                    base_obj["configs_to_do"] = configs_to_do_value
                    forced_removed_any = True

                # Keep this key global-only to avoid hidden per-test conflicts.
                for group_obj in groups_obj.values():
                    if isinstance(group_obj, dict) and "configs_to_do" in group_obj:
                        group_obj.pop("configs_to_do", None)
                        forced_removed_any = True

        removed_any = False
        for path, spec in self.field_specs.items():
            try:
                parsed_value = self._parse_form_value(path, spec)
            except Exception as exc:
                messagebox.showerror("Input error", f"Invalid value for '{path}':\n{exc}")
                return False
            if parsed_value is DELETE_FIELD:
                self._delete_value_by_path(section, path)
                removed_any = True
                continue
            self._set_value_by_path(section, path, parsed_value)

        if len(pending_removed_groups) > 0 and isinstance(section.get("groups"), dict):
            groups_obj = section["groups"]
            for group_name in pending_removed_groups:
                if group_name in groups_obj:
                    groups_obj.pop(group_name, None)
                    removed_any = True
            self.matrix_removed_groups.difference_update(pending_removed_groups)

        removed_any = removed_any or forced_removed_any
        if removed_any:
            self._build_form()
        self._populate_raw_text()
        self._notify_configs_to_do_preview()
        self.status_cb("Applied form values to YAML.")
        if show_success:
            messagebox.showinfo("Applied", "Form values applied to YAML.")
        return True

    def apply_yaml_text(self, show_success=True) -> bool:
        raw = self.raw_text.get("1.0", tk.END)
        try:
            parsed = yaml.safe_load(raw) or {}
        except Exception as exc:
            messagebox.showerror("YAML error", f"Failed to parse YAML text:\n{exc}")
            return False

        if not isinstance(parsed, dict):
            messagebox.showerror("Unsupported format", "Top-level YAML content must be a mapping/object.")
            return False

        self.config_data = parsed
        self._reset_matrix_state()
        self._refresh_sections()
        self._build_form()
        self.status_cb("Applied raw YAML text to form.")
        if show_success:
            messagebox.showinfo("Applied", "YAML text applied to form.")
        return True


class GeneratorPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        controls = ttk.LabelFrame(self, text="Instance Generator")
        controls.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        controls.columnconfigure(1, weight=1)

        self.config_var = tk.StringVar(value="configs/master_generator_config.yaml")
        self.output_var = tk.StringVar(value="instances")
        self.overwrite_var = tk.BooleanVar(value=False)

        quick = ttk.Frame(controls)
        quick.grid(row=0, column=0, columnspan=4, sticky="ew", padx=8, pady=(8, 2))
        ttk.Button(
            quick,
            text="Master preset",
            command=lambda: self.config_var.set("configs/master_generator_config.yaml")).pack(side="left")
        ttk.Button(
            quick,
            text="Subproblem preset",
            command=lambda: self.config_var.set("configs/subproblem_generator_config.yaml")).pack(side="left", padx=(6, 0))

        ttk.Label(controls, text="Config file").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.config_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=self._browse_config).grid(row=1, column=2, padx=6, pady=4)

        ttk.Label(controls, text="Output dir").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.output_var).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=self._browse_output).grid(row=2, column=2, padx=6, pady=4)

        ttk.Checkbutton(controls, text="Overwrite existing output", variable=self.overwrite_var).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8, pady=6)

        actions = ttk.Frame(controls)
        actions.grid(row=3, column=2, sticky="e", padx=8, pady=6)
        ttk.Button(actions, text="Run generator", command=self._run).pack(side="left")
        ttk.Button(actions, text="Open output", command=self._open_output).pack(side="left", padx=(6, 0))

        self.editor = ConfigEditor(
            self,
            project_root=self.app.project_root,
            path_var=self.config_var,
            status_cb=self.app.set_status)
        self.editor.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

    def _browse_config(self):
        self.app.browse_file(self.config_var, [("YAML files", "*.yaml *.yml"), ("All files", "*.*")])

    def _browse_output(self):
        self.app.browse_directory(self.output_var)

    def _run(self):
        config = self.app.resolve_path(self.config_var.get())
        output = self.app.resolve_path(self.output_var.get())

        cmd = [
            self.app.runner_python,
            "generator.py",
            "-c", str(config),
            "-o", str(output)]
        if self.overwrite_var.get():
            cmd.append("--overwrite")
        self.app.start_command(cmd)

    def _open_output(self):
        self.app.open_path(self.app.resolve_path(self.output_var.get()))


class SolverRunPanel(ttk.Frame):
    def __init__(
            self,
            parent,
            app,
            title: str,
            script_name: str,
            default_config: str,
            default_input: str,
            default_output: str):
        super().__init__(parent)
        self.app = app
        self.script_name = script_name

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        controls = ttk.LabelFrame(self, text=title)
        controls.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        controls.columnconfigure(1, weight=1)

        self.config_var = tk.StringVar(value=default_config)
        self.input_var = tk.StringVar(value=default_input)
        self.output_var = tk.StringVar(value=default_output)
        self.tests_to_run_var = tk.StringVar(value="-")
        self.available_group_var = tk.StringVar(value="")
        self.available_instance_var = tk.StringVar(value="")
        self.input_index_summary_var = tk.StringVar(value="-")
        self.overwrite_var = tk.BooleanVar(value=False)
        self.verbose_var = tk.BooleanVar(value=False)
        self.available_inputs: dict[str, list[str]] = {}
        self._input_scan_after_id: str | None = None

        ttk.Label(controls, text="Config file").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.config_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=lambda: self.app.browse_file(
            self.config_var,
            [("YAML files", "*.yaml *.yml"), ("All files", "*.*")])).grid(row=0, column=2, padx=6, pady=4)

        ttk.Label(controls, text="Input dir").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.input_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=self._browse_input).grid(
            row=1, column=2, padx=6, pady=4)

        ttk.Label(controls, text="Available group").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        self.available_group_combo = ttk.Combobox(
            controls,
            textvariable=self.available_group_var,
            state="readonly")
        self.available_group_combo.grid(row=2, column=1, sticky="ew", pady=4)
        self.available_group_combo.bind("<<ComboboxSelected>>", self._on_available_group_change)
        ttk.Button(controls, text="Refresh", command=self.refresh_input_index).grid(
            row=2, column=2, padx=6, pady=4)

        ttk.Label(controls, text="Available instance").grid(row=3, column=0, sticky="w", padx=8, pady=4)
        self.available_instance_combo = ttk.Combobox(
            controls,
            textvariable=self.available_instance_var,
            state="readonly")
        self.available_instance_combo.grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Label(controls, textvariable=self.input_index_summary_var).grid(
            row=3, column=2, sticky="w", padx=6, pady=4)

        ttk.Label(controls, text="Output dir").grid(row=4, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.output_var).grid(row=4, column=1, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=lambda: self.app.browse_directory(self.output_var)).grid(
            row=4, column=2, padx=6, pady=4)

        ttk.Label(controls, text="Test configs to run").grid(row=5, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.tests_to_run_var, state="readonly").grid(
            row=5, column=1, sticky="ew", pady=4)
        ttk.Label(controls, text="([all] = tutti)").grid(row=5, column=2, sticky="w", padx=6, pady=4)

        ttk.Checkbutton(controls, text="Overwrite existing output", variable=self.overwrite_var).grid(
            row=6, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(controls, text="Verbose solver output", variable=self.verbose_var).grid(
            row=6, column=1, sticky="w", padx=8, pady=6)

        actions = ttk.Frame(controls)
        actions.grid(row=6, column=2, sticky="e", padx=8, pady=6)
        ttk.Button(actions, text=f"Run {script_name}", command=self._run).pack(side="left")
        ttk.Button(actions, text="Open output", command=lambda: self.app.open_path(
            self.app.resolve_path(self.output_var.get()))).pack(side="left", padx=(6, 0))

        self.editor = ConfigEditor(
            self,
            project_root=self.app.project_root,
            path_var=self.config_var,
            status_cb=self.app.set_status,
            on_configs_to_do_preview_cb=self._on_configs_to_do_preview)
        self.editor.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        self.input_var.trace_add("write", self._on_input_var_change)
        self.refresh_input_index()

    def _on_configs_to_do_preview(self, preview: str):
        self.tests_to_run_var.set(preview)

    def _browse_input(self):
        self.app.browse_directory(self.input_var)
        self.refresh_input_index()

    def _on_input_var_change(self, *_args):
        if self._input_scan_after_id is not None:
            self.after_cancel(self._input_scan_after_id)
        self._input_scan_after_id = self.after(250, self.refresh_input_index)

    def _on_available_group_change(self, _event=None):
        self._refresh_available_instance_values()

    def refresh_input_index(self):
        self._input_scan_after_id = None
        input_dir = self.app.resolve_path(self.input_var.get())
        self.available_inputs = {}

        if not input_dir.exists():
            self.available_group_combo["values"] = []
            self.available_instance_combo["values"] = []
            self.available_group_var.set("")
            self.available_instance_var.set("")
            self.input_index_summary_var.set("Input dir non trovata")
            return

        if not input_dir.is_dir():
            self.available_group_combo["values"] = []
            self.available_instance_combo["values"] = []
            self.available_group_var.set("")
            self.available_instance_var.set("")
            self.input_index_summary_var.set("Input path non valido")
            return

        for group_dir in sorted(input_dir.iterdir()):
            if not group_dir.is_dir():
                continue
            instance_names = sorted(
                instance_path.stem
                for instance_path in group_dir.glob("*.json")
                if instance_path.is_file())
            self.available_inputs[group_dir.name] = instance_names

        group_names = list(self.available_inputs.keys())
        self.available_group_combo["values"] = group_names

        if not group_names:
            self.available_group_var.set("")
            self.available_instance_combo["values"] = []
            self.available_instance_var.set("")
            self.input_index_summary_var.set("0 gruppi trovati")
            return

        current_group = self.available_group_var.get()
        if current_group not in self.available_inputs:
            self.available_group_var.set(group_names[0])

        total_instances = sum(len(instance_names) for instance_names in self.available_inputs.values())
        self.input_index_summary_var.set(f"{len(group_names)} gruppi, {total_instances} istanze")
        self._refresh_available_instance_values()

    def _refresh_available_instance_values(self):
        selected_group = self.available_group_var.get()
        instance_names = self.available_inputs.get(selected_group, [])
        self.available_instance_combo["values"] = instance_names

        if not instance_names:
            self.available_instance_var.set("")
            return

        current_instance = self.available_instance_var.get()
        if current_instance not in instance_names:
            self.available_instance_var.set(instance_names[0])

    def _run(self):
        config = self.app.resolve_path(self.config_var.get())
        input_dir = self.app.resolve_path(self.input_var.get())
        output_dir = self.app.resolve_path(self.output_var.get())
        cmd = [
            self.app.runner_python,
            self.script_name,
            "-c", str(config),
            "-i", str(input_dir),
            "-o", str(output_dir)]
        if self.overwrite_var.get():
            cmd.append("--overwrite")
        if self.verbose_var.get():
            cmd.append("--verbose")
        self.app.start_command(cmd)


class SolvingPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        tabs = ttk.Notebook(self)
        tabs.grid(row=0, column=0, sticky="nsew")

        iterative = SolverRunPanel(
            tabs,
            app=self.app,
            title="Iterative Solver",
            script_name="solver.py",
            default_config="configs/iterative_solver_config.yaml",
            default_input="instances",
            default_output="results")
        tabs.add(iterative, text="Iterative")

        single_pass = SolverRunPanel(
            tabs,
            app=self.app,
            title="Single-Pass Solver",
            script_name="single_pass_solver.py",
            default_config="configs/single_pass_solver_config.yaml",
            default_input="instances",
            default_output="single_pass_results")
        tabs.add(single_pass, text="Single-pass")


class ResultsBrowser(ttk.LabelFrame):
    def __init__(self, parent, app):
        super().__init__(parent, text="Results Browser")
        self.app = app

        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        self.results_root_var = tk.StringVar(value="results")
        self.filter_config_var = tk.StringVar(value="All")
        self.filter_group_var = tk.StringVar(value="All")
        self.filter_instance_var = tk.StringVar(value="All")
        self.filter_type_var = tk.StringVar(value="Plots (.png)")
        self.filter_plot_scope_var = tk.StringVar(value="All")

        self.result_dirs: list[tuple[str, str, str, Path]] = []
        self.file_index: dict[str, Path] = {}

        root_row = ttk.Frame(self)
        root_row.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        root_row.columnconfigure(1, weight=1)

        ttk.Label(root_row, text="Results root").grid(row=0, column=0, padx=(0, 6), sticky="w")
        ttk.Entry(root_row, textvariable=self.results_root_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(root_row, text="Browse", command=lambda: self.app.browse_directory(self.results_root_var)).grid(
            row=0, column=2, padx=(6, 0))
        ttk.Button(root_row, text="Refresh", command=self.refresh).grid(row=0, column=3, padx=(6, 0))

        filters = ttk.Frame(self)
        filters.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        for idx in [1, 3, 5, 7, 9]:
            filters.columnconfigure(idx, weight=1)

        ttk.Label(filters, text="Config").grid(row=0, column=0, padx=(0, 6), sticky="w")
        self.config_combo = ttk.Combobox(filters, textvariable=self.filter_config_var, state="readonly")
        self.config_combo.grid(row=0, column=1, sticky="ew")
        self.config_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_filter_change())

        ttk.Label(filters, text="Group").grid(row=0, column=2, padx=(8, 6), sticky="w")
        self.group_combo = ttk.Combobox(filters, textvariable=self.filter_group_var, state="readonly")
        self.group_combo.grid(row=0, column=3, sticky="ew")
        self.group_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_filter_change())

        ttk.Label(filters, text="Instance").grid(row=0, column=4, padx=(8, 6), sticky="w")
        self.instance_combo = ttk.Combobox(filters, textvariable=self.filter_instance_var, state="readonly")
        self.instance_combo.grid(row=0, column=5, sticky="ew")
        self.instance_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_file_list())

        ttk.Label(filters, text="File type").grid(row=0, column=6, padx=(8, 6), sticky="w")
        self.type_combo = ttk.Combobox(
            filters,
            textvariable=self.filter_type_var,
            state="readonly",
            values=[
                "Plots (.png)",
                "Analysis tables (.xlsx/.csv)",
                "JSON results",
                "Logs (.log)",
                "All files"])
        self.type_combo.grid(row=0, column=7, sticky="ew")
        self.type_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_file_list())

        ttk.Label(filters, text="Plot scope").grid(row=0, column=8, padx=(8, 6), sticky="w")
        self.plot_scope_combo = ttk.Combobox(
            filters,
            textvariable=self.filter_plot_scope_var,
            state="readonly",
            values=["All", "Comparison", "Group", "Run"])
        self.plot_scope_combo.grid(row=0, column=9, sticky="ew")
        self.plot_scope_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_file_list())

        action_row = ttk.Frame(self)
        action_row.grid(row=2, column=0, sticky="ew", padx=8, pady=(2, 6))
        ttk.Button(action_row, text="Open selected", command=self.open_selected).pack(side="left")
        ttk.Button(action_row, text="Open parent folder", command=self.open_selected_parent).pack(side="left", padx=(6, 0))

        tree_frame = ttk.Frame(self)
        tree_frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=(0, 8))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=("type", "path"), show="headings", selectmode="browse")
        self.tree.heading("type", text="Type")
        self.tree.heading("path", text="File")
        self.tree.column("type", width=120, anchor="w")
        self.tree.column("path", anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _e: self.open_selected())

        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=yscroll.set)

        self.count_label = ttk.Label(self, text="0 files")
        self.count_label.grid(row=4, column=0, sticky="w", padx=8, pady=(0, 8))

        self.refresh()

    def _results_root(self) -> Path:
        return self.app.resolve_path(self.results_root_var.get())

    def refresh(self):
        root = self._results_root()
        self.result_dirs.clear()
        self.file_index.clear()

        if root.exists():
            for entry in sorted(root.iterdir()):
                if not entry.is_dir():
                    continue
                tokens = entry.name.split("__")
                if len(tokens) != 3:
                    continue
                self.result_dirs.append((tokens[0], tokens[1], tokens[2], entry))

        configs = sorted({d[0] for d in self.result_dirs})
        groups = sorted({d[1] for d in self.result_dirs})
        instances = sorted({d[2] for d in self.result_dirs})

        self.config_combo["values"] = ["All"] + configs
        self.group_combo["values"] = ["All"] + groups
        self.instance_combo["values"] = ["All"] + instances

        self.filter_config_var.set("All")
        self.filter_group_var.set("All")
        self.filter_instance_var.set("All")
        self.filter_plot_scope_var.set("All")
        self._refresh_file_list()

    def _on_filter_change(self):
        selected_config = self.filter_config_var.get()
        selected_group = self.filter_group_var.get()

        dirs = self.result_dirs
        if selected_config != "All":
            dirs = [d for d in dirs if d[0] == selected_config]
        if selected_group != "All":
            dirs = [d for d in dirs if d[1] == selected_group]

        instances = sorted({d[2] for d in dirs})
        self.instance_combo["values"] = ["All"] + instances
        if self.filter_instance_var.get() not in self.instance_combo["values"]:
            self.filter_instance_var.set("All")
        self._refresh_file_list()

    def _selected_dirs(self) -> list[Path]:
        selected_config = self.filter_config_var.get()
        selected_group = self.filter_group_var.get()
        selected_instance = self.filter_instance_var.get()

        dirs = self.result_dirs
        if selected_config != "All":
            dirs = [d for d in dirs if d[0] == selected_config]
        if selected_group != "All":
            dirs = [d for d in dirs if d[1] == selected_group]
        if selected_instance != "All":
            dirs = [d for d in dirs if d[2] == selected_instance]
        return [d[3] for d in dirs]

    def _refresh_file_list(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.file_index.clear()

        root = self._results_root()
        selected_dirs = self._selected_dirs()
        category = self.filter_type_var.get()
        plot_scope = self.filter_plot_scope_var.get()

        files: list[tuple[str, Path]] = []

        if category == "Analysis tables (.xlsx/.csv)":
            self.plot_scope_combo.configure(state="disabled")
            analysis_dir = root.joinpath("analysis")
            if analysis_dir.exists():
                files.extend([("analysis", path) for path in sorted(analysis_dir.rglob("*.xlsx"))])
                files.extend([("analysis", path) for path in sorted(analysis_dir.rglob("*.csv"))])
        elif category == "Plots (.png)":
            self.plot_scope_combo.configure(state="readonly")
            aggregate_plots_dir = root.joinpath("plots")
            if aggregate_plots_dir.exists():
                if plot_scope in {"All", "Comparison"}:
                    files.extend([
                        ("plot", path)
                        for path in sorted(aggregate_plots_dir.rglob("*.png"))
                        if "groups" not in path.parts
                    ])
                if plot_scope in {"All", "Group"}:
                    group_root = aggregate_plots_dir.joinpath("groups")
                    if group_root.exists():
                        files.extend([("plot", path) for path in sorted(group_root.rglob("*.png"))])
        else:
            self.plot_scope_combo.configure(state="disabled")

        for result_dir in selected_dirs:
            if category == "Plots (.png)":
                if plot_scope in {"All", "Run"}:
                    files.extend([("plot", path) for path in sorted(result_dir.glob("plots/**/*.png"))])
            elif category == "JSON results":
                files.extend([("json", path) for path in sorted(result_dir.glob("*.json"))])
                files.extend([("json", path) for path in sorted(result_dir.glob("iter_*/*.json"))])
            elif category == "Logs (.log)":
                files.extend([("log", path) for path in sorted(result_dir.glob("*.log"))])
                files.extend([("log", path) for path in sorted(result_dir.glob("iter_*/*.log"))])
            elif category == "All files":
                files.extend([("file", path) for path in sorted(result_dir.rglob("*")) if path.is_file()])

        if category == "All files":
            analysis_dir = root.joinpath("analysis")
            if analysis_dir.exists():
                files.extend([("analysis", path) for path in sorted(analysis_dir.rglob("*")) if path.is_file()])
            aggregate_plots_dir = root.joinpath("plots")
            if aggregate_plots_dir.exists():
                files.extend([("plot", path) for path in sorted(aggregate_plots_dir.rglob("*")) if path.is_file()])

        unique_files = sorted(set(files), key=lambda item: str(item[1]))

        for idx, (file_type, path) in enumerate(unique_files):
            iid = f"file_{idx}"
            try:
                shown = str(path.relative_to(root))
            except ValueError:
                shown = str(path)
            self.tree.insert("", "end", iid=iid, values=(file_type, shown))
            self.file_index[iid] = path

        self.count_label.configure(text=f"{len(unique_files)} files")

    def _selected_path(self) -> Path | None:
        selection = self.tree.selection()
        if len(selection) == 0:
            return None
        return self.file_index.get(selection[0])

    def open_selected(self):
        path = self._selected_path()
        if path is None:
            messagebox.showwarning("No selection", "Select a file first.")
            return
        self.app.open_path(path)

    def open_selected_parent(self):
        path = self._selected_path()
        if path is None:
            messagebox.showwarning("No selection", "Select a file first.")
            return
        self.app.open_path(path.parent)


class PlotSelectionPanel(ttk.LabelFrame):
    def __init__(self, parent, title: str, plot_names: list[str], on_load, on_apply, columns=2):
        super().__init__(parent, text=title)
        self.plot_names = list(plot_names)
        self.on_load = on_load
        self.on_apply = on_apply
        self.columns = max(1, columns)
        self.plot_vars: dict[str, tk.BooleanVar] = {
            plot_name: tk.BooleanVar(value=False)
            for plot_name in self.plot_names
        }

        self.columnconfigure(0, weight=1)

        actions = ttk.Frame(self)
        actions.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        ttk.Button(actions, text="Load from YAML", command=self.on_load).pack(side="left")
        ttk.Button(actions, text="Apply to YAML", command=self.on_apply).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Select all", command=self.select_all).pack(side="left", padx=(12, 0))
        ttk.Button(actions, text="Clear all", command=self.clear_all).pack(side="left", padx=(6, 0))

        grid_frame = ttk.Frame(self)
        grid_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        for column_index in range(self.columns):
            grid_frame.columnconfigure(column_index, weight=1)

        for index, plot_name in enumerate(self.plot_names):
            row_index = index // self.columns
            column_index = index % self.columns
            ttk.Checkbutton(
                grid_frame,
                text=plot_name,
                variable=self.plot_vars[plot_name]).grid(
                    row=row_index,
                    column=column_index,
                    sticky="w",
                    padx=(0, 12),
                    pady=2)

    def get_selected(self) -> list[str]:
        return [plot_name for plot_name in self.plot_names if self.plot_vars[plot_name].get()]

    def set_selected(self, selected_plot_names: list[str]):
        selected = {str(name) for name in selected_plot_names}
        for plot_name, variable in self.plot_vars.items():
            variable.set(plot_name in selected)

    def select_all(self):
        for variable in self.plot_vars.values():
            variable.set(True)

    def clear_all(self):
        for variable in self.plot_vars.values():
            variable.set(False)


class ResultPlotLevelPanel(ttk.Frame):
    def __init__(self, parent, level: str, specs: list[dict], on_load, on_apply):
        super().__init__(parent)
        self.level = level
        self.specs = list(specs)
        self.on_load = on_load
        self.on_apply = on_apply
        self.plot_vars: dict[str, tk.BooleanVar] = {
            str(spec['key']): tk.BooleanVar(value=bool(spec.get('default_enabled', False)))
            for spec in self.specs
        }

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        actions = ttk.Frame(self)
        actions.grid(row=0, column=0, sticky='ew', padx=8, pady=(8, 4))
        ttk.Button(actions, text='Load from YAML', command=self.on_load).pack(side='left')
        ttk.Button(actions, text='Apply to YAML', command=self.on_apply).pack(side='left', padx=(6, 0))
        ttk.Button(actions, text='Select all', command=self.select_all).pack(side='left', padx=(12, 0))
        ttk.Button(actions, text='Clear all', command=self.clear_all).pack(side='left', padx=(6, 0))

        outer = ttk.Frame(self)
        outer.grid(row=1, column=0, sticky='nsew', padx=8, pady=(0, 8))
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        canvas.configure(yscrollcommand=scrollbar.set)

        inner = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.columnconfigure(0, weight=0, minsize=72)
        inner.columnconfigure(1, weight=0, minsize=260)
        inner.columnconfigure(2, weight=1, minsize=460)

        ttk.Label(inner, text='Run', font=('TkDefaultFont', 9, 'bold')).grid(
            row=0, column=0, sticky='w', padx=(4, 8), pady=(2, 6))
        ttk.Label(inner, text='Plot', font=('TkDefaultFont', 9, 'bold')).grid(
            row=0, column=1, sticky='w', padx=(4, 8), pady=(2, 6))
        ttk.Label(inner, text='Description', font=('TkDefaultFont', 9, 'bold')).grid(
            row=0, column=2, sticky='w', padx=(4, 8), pady=(2, 6))

        for row_index, spec in enumerate(self.specs, start=1):
            key = str(spec['key'])
            ttk.Checkbutton(inner, variable=self.plot_vars[key]).grid(
                row=row_index, column=0, sticky='w', padx=(4, 8), pady=3)
            ttk.Label(inner, text=str(spec.get('display_name', key))).grid(
                row=row_index, column=1, sticky='w', padx=(4, 8), pady=3)
            ttk.Label(
                inner,
                text=str(spec.get('short_description', '')),
                foreground='#555',
                wraplength=560,
                justify='left').grid(
                    row=row_index, column=2, sticky='ew', padx=(4, 8), pady=3)

        def _on_inner_configure(_event=None):
            canvas.configure(scrollregion=canvas.bbox('all'))

        def _on_canvas_configure(event=None):
            if event is None:
                return
            canvas.itemconfig(window, width=max(event.width, inner.winfo_reqwidth()))

        inner.bind('<Configure>', _on_inner_configure)
        canvas.bind('<Configure>', _on_canvas_configure)

    def get_selected_keys(self) -> list[str]:
        return [
            str(spec['key'])
            for spec in self.specs
            if self.plot_vars[str(spec['key'])].get()
        ]

    def set_selected_keys(self, selected_keys: list[str]):
        selected = {str(name) for name in selected_keys}
        for spec in self.specs:
            key = str(spec['key'])
            self.plot_vars[key].set(key in selected)

    def select_all(self):
        for variable in self.plot_vars.values():
            variable.set(True)

    def clear_all(self):
        for variable in self.plot_vars.values():
            variable.set(False)


class ResultPlotSelectionPanel(ttk.LabelFrame):
    def __init__(self, parent, title: str, on_load, on_apply):
        super().__init__(parent, text=title)
        self.on_load = on_load
        self.on_apply = on_apply
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky='nsew', padx=8, pady=8)

        self.level_panels: dict[str, ResultPlotLevelPanel] = {}
        level_titles = {
            'comparison': 'Comparison',
            'group': 'Group',
            'run': 'Run',
        }
        for level in RESULT_PLOT_LEVELS:
            panel = ResultPlotLevelPanel(
                self.notebook,
                level=level,
                specs=get_result_plot_specs(level),
                on_load=self.on_load,
                on_apply=self.on_apply)
            self.level_panels[level] = panel
            self.notebook.add(panel, text=level_titles.get(level, level.title()))

    def get_selected_map(self) -> dict[str, list[str]]:
        return {
            level: panel.get_selected_keys()
            for level, panel in self.level_panels.items()
        }

    def set_selected_map(self, selected_map: dict[str, list[str]]):
        normalized = parse_result_plots_to_do(selected_map)
        for level, panel in self.level_panels.items():
            panel.set_selected_keys(normalized.get(level, []))


class PngPreviewWindow(tk.Toplevel):
    def __init__(self, parent, app, image_path: Path):
        super().__init__(parent)
        self.app = app
        self.image_path = image_path
        self.image: tk.PhotoImage | None = None

        self.title(f"Plot preview - {image_path.name}")
        self.minsize(720, 480)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, text=str(image_path), foreground="#555").grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="Open externally", command=lambda: self.app.open_path(image_path)).grid(
            row=0, column=1, padx=(8, 0))
        ttk.Button(toolbar, text="Refresh", command=self._load_image).grid(row=0, column=2, padx=(6, 0))

        preview_frame = ttk.Frame(self)
        preview_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(preview_frame, background="#f4f4f4", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        xscroll = ttk.Scrollbar(preview_frame, orient="horizontal", command=self.canvas.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        yscroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.canvas.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)

        self._load_image()

    def _load_image(self):
        self.canvas.delete("all")
        self.image = None

        if not self.image_path.exists():
            self.canvas.create_text(
                20,
                20,
                anchor="nw",
                text=f"File not found:\n{self.image_path}",
                fill="#333")
            self.canvas.configure(scrollregion=(0, 0, 640, 240))
            return

        try:
            self.image = tk.PhotoImage(file=str(self.image_path))
        except Exception as exc:
            self.canvas.create_text(
                20,
                20,
                anchor="nw",
                text=f"Cannot preview PNG internally.\n{exc}\nUse 'Open externally'.",
                fill="#333")
            self.canvas.configure(scrollregion=(0, 0, 720, 260))
            return

        image_width = self.image.width()
        image_height = self.image.height()
        self.canvas.create_image(0, 0, anchor="nw", image=self.image)
        self.canvas.configure(scrollregion=(0, 0, image_width, image_height))

        window_width = min(max(image_width + 40, 760), 1440)
        window_height = min(max(image_height + 100, 520), 960)
        self.geometry(f"{window_width}x{window_height}")


class InstancePlotsBrowser(ttk.LabelFrame):
    def __init__(self, parent, app, instances_root_var: tk.StringVar):
        super().__init__(parent, text="Instance Plot Browser")
        self.app = app
        self.instances_root_var = instances_root_var
        self.scope_var = tk.StringVar(value="Aggregated")
        self.group_var = tk.StringVar(value="")
        self.instance_var = tk.StringVar(value="")
        self.count_var = tk.StringVar(value="0 plots")

        self.group_instances: dict[str, list[str]] = {}
        self.file_index: dict[str, Path] = {}
        self.preview_windows: list[PngPreviewWindow] = []
        self._refresh_after_id: str | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        for column_index in [1, 3, 5]:
            controls.columnconfigure(column_index, weight=1)

        ttk.Label(controls, text="Plots root").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.plots_root_entry = ttk.Entry(controls, state="readonly")
        self.plots_root_entry.grid(row=0, column=1, columnspan=5, sticky="ew")

        ttk.Label(controls, text="Scope").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.scope_combo = ttk.Combobox(
            controls,
            textvariable=self.scope_var,
            state="readonly",
            values=["Aggregated", "Group + instance"])
        self.scope_combo.grid(row=1, column=1, sticky="ew")
        self.scope_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_file_list())

        ttk.Label(controls, text="Group").grid(row=1, column=2, sticky="w", padx=(8, 6))
        self.group_combo = ttk.Combobox(controls, textvariable=self.group_var, state="readonly")
        self.group_combo.grid(row=1, column=3, sticky="ew")
        self.group_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_group_change())

        ttk.Label(controls, text="Instance").grid(row=1, column=4, sticky="w", padx=(8, 6))
        self.instance_combo = ttk.Combobox(controls, textvariable=self.instance_var, state="readonly")
        self.instance_combo.grid(row=1, column=5, sticky="ew")
        self.instance_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_file_list())

        actions = ttk.Frame(self)
        actions.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))
        ttk.Button(actions, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(actions, text="Preview selected", command=self.preview_selected).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Open selected", command=self.open_selected).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Open folder", command=self.open_selected_parent).pack(side="left", padx=(6, 0))
        ttk.Label(actions, textvariable=self.count_var).pack(side="right")

        tree_frame = ttk.Frame(self)
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=("file",), show="headings", selectmode="browse")
        self.tree.heading("file", text="PNG file")
        self.tree.column("file", anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _event: self.preview_selected())

        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=yscroll.set)

        self.instances_root_var.trace_add("write", self._schedule_refresh)
        self.refresh()

    def _schedule_refresh(self, *_args):
        if self._refresh_after_id is not None:
            self.after_cancel(self._refresh_after_id)
        self._refresh_after_id = self.after(250, self.refresh)

    def _plots_root(self) -> Path:
        return self.app.resolve_path(self.instances_root_var.get()).joinpath("plots_instances")

    def refresh(self):
        self._refresh_after_id = None
        plots_root = self._plots_root()
        self.plots_root_entry.configure(state="normal")
        self.plots_root_entry.delete(0, tk.END)
        self.plots_root_entry.insert(0, str(plots_root))
        self.plots_root_entry.configure(state="readonly")

        self.group_instances = {}
        if plots_root.exists():
            for group_dir in sorted(path for path in plots_root.iterdir() if path.is_dir()):
                instance_dirs = sorted(path.name for path in group_dir.iterdir() if path.is_dir())
                if len(instance_dirs) > 0:
                    self.group_instances[group_dir.name] = instance_dirs

        group_names = list(self.group_instances.keys())
        self.group_combo["values"] = group_names
        if self.group_var.get() not in group_names:
            self.group_var.set(group_names[0] if group_names else "")
        self._refresh_instance_values()
        self._refresh_file_list()

    def _refresh_instance_values(self):
        instance_names = self.group_instances.get(self.group_var.get(), [])
        self.instance_combo["values"] = instance_names
        if self.instance_var.get() not in instance_names:
            self.instance_var.set(instance_names[0] if instance_names else "")

    def _on_group_change(self):
        self._refresh_instance_values()
        self._refresh_file_list()

    def _selected_files(self) -> list[Path]:
        plots_root = self._plots_root()
        if not plots_root.exists():
            return []

        if self.scope_var.get() == "Aggregated":
            self.group_combo.configure(state="disabled")
            self.instance_combo.configure(state="disabled")
            return sorted(path for path in plots_root.glob("*.png") if path.is_file())

        self.group_combo.configure(state="readonly")
        self.instance_combo.configure(state="readonly")
        group_name = self.group_var.get()
        instance_name = self.instance_var.get()
        if group_name == "" or instance_name == "":
            return []

        instance_dir = plots_root.joinpath(group_name, instance_name)
        if not instance_dir.exists():
            return []
        return sorted(path for path in instance_dir.glob("*.png") if path.is_file())

    def _refresh_file_list(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.file_index.clear()

        files = self._selected_files()
        for index, path in enumerate(files):
            iid = f"plot_{index}"
            self.tree.insert("", "end", iid=iid, values=(path.name,))
            self.file_index[iid] = path
        self.count_var.set(f"{len(files)} plots")

    def _selected_path(self) -> Path | None:
        selection = self.tree.selection()
        if len(selection) == 0:
            return None
        return self.file_index.get(selection[0])

    def preview_selected(self):
        path = self._selected_path()
        if path is None:
            messagebox.showwarning("No selection", "Select a PNG file first.")
            return
        preview_window = PngPreviewWindow(self, self.app, path)
        self.preview_windows.append(preview_window)

    def open_selected(self):
        path = self._selected_path()
        if path is None:
            messagebox.showwarning("No selection", "Select a PNG file first.")
            return
        self.app.open_path(path)

    def open_selected_parent(self):
        path = self._selected_path()
        if path is None:
            messagebox.showwarning("No selection", "Select a PNG file first.")
            return
        self.app.open_path(path.parent)


class AnalysisPlotPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        tabs = ttk.Notebook(self)
        tabs.grid(row=0, column=0, sticky="nsew")

        analyzer_tab = ttk.Frame(tabs)
        analyzer_tab.columnconfigure(0, weight=1)
        analyzer_tab.rowconfigure(1, weight=1)
        tabs.add(analyzer_tab, text="Analyzer")
        self._build_analyzer_tab(analyzer_tab)

        result_plotter_tab = ttk.Frame(tabs)
        result_plotter_tab.columnconfigure(0, weight=1)
        result_plotter_tab.rowconfigure(1, weight=1)
        tabs.add(result_plotter_tab, text="Result plots")
        self._build_result_plotter_tab(result_plotter_tab)

        instance_plotter_tab = ttk.Frame(tabs)
        instance_plotter_tab.columnconfigure(0, weight=1)
        instance_plotter_tab.rowconfigure(1, weight=1)
        tabs.add(instance_plotter_tab, text="Instance plots")
        self._build_instance_plotter_tab(instance_plotter_tab)

        browser_tab = ttk.Frame(tabs)
        browser_tab.columnconfigure(0, weight=1)
        browser_tab.rowconfigure(0, weight=1)
        tabs.add(browser_tab, text="Browse result files")
        self.browser = ResultsBrowser(browser_tab, self.app)
        self.browser.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        instance_browser_tab = ttk.Frame(tabs)
        instance_browser_tab.columnconfigure(0, weight=1)
        instance_browser_tab.rowconfigure(0, weight=1)
        tabs.add(instance_browser_tab, text="Browse instance plots")
        self.instance_plot_browser = InstancePlotsBrowser(instance_browser_tab, self.app, self.master_plot_input_var)
        self.instance_plot_browser.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

    def _load_analyzer_base_config(self) -> dict:
        target_path = self.app.resolve_path(self.analyzer_config_var.get())
        if hasattr(self, "analyzer_editor") and self.analyzer_editor.current_file == target_path:
            if isinstance(self.analyzer_editor.config_data, dict):
                return dict(self.analyzer_editor.config_data)

        if not target_path.exists():
            return {}

        try:
            with open(target_path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
        except Exception:
            return {}

        return loaded if isinstance(loaded, dict) else {}

    def _analyzer_group_sort_key(self, group_name: str) -> tuple[int, int, int, str]:
        match = re.search(r"(?P<patients>\d+)pat_(?P<care_units>\d+)cu(?:_(?P<operators>\d+)op)?", group_name)
        if match is None:
            return (10**9, 10**9, 10**9, group_name)
        operators = match.group("operators")
        return (
            int(match.group("patients")),
            int(match.group("care_units")),
            int(operators) if operators is not None else 10**9,
            group_name,
        )

    def _result_plot_instance_sort_key(self, instance_name: str) -> tuple[int, str]:
        match = re.search(r"inst_(\d+)", instance_name)
        if match is None:
            return (10**9, instance_name)
        return (int(match.group(1)), instance_name)

    def _scan_analyzer_available_filters(self) -> tuple[list[str], list[str]]:
        input_root = self.app.resolve_path(self.analyzer_input_var.get())
        if not input_root.exists():
            return [], []

        config_names: set[str] = set()
        group_names: set[str] = set()
        for entry in sorted(input_root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in {"analysis", "plots"}:
                continue
            tokens = entry.name.split("__")
            if len(tokens) != 3:
                continue
            config_names.add(tokens[0])
            group_names.add(tokens[1])

        ordered_configs = self._order_result_plot_config_names(
            list(config_names),
            self.analyzer_editor.config_data if hasattr(self, "analyzer_editor") else None)
        ordered_groups = sorted(group_names, key=self._analyzer_group_sort_key)
        return ordered_configs, ordered_groups

    def _get_available_analyzer_configs(self) -> list[str]:
        return [self.analyzer_config_listbox.get(index) for index in range(self.analyzer_config_listbox.size())]

    def _get_available_analyzer_groups(self) -> list[str]:
        return [self.analyzer_group_listbox.get(index) for index in range(self.analyzer_group_listbox.size())]

    def _get_selected_analyzer_configs(self) -> list[str]:
        return [self.analyzer_config_listbox.get(index) for index in self.analyzer_config_listbox.curselection()]

    def _get_selected_analyzer_groups(self) -> list[str]:
        return [self.analyzer_group_listbox.get(index) for index in self.analyzer_group_listbox.curselection()]

    def _set_selected_analyzer_filters(self, selected_configs: list[str], selected_groups: list[str]):
        selected_config_set = {str(name) for name in selected_configs}
        selected_group_set = {str(name) for name in selected_groups}

        self.analyzer_config_listbox.selection_clear(0, tk.END)
        for index, config_name in enumerate(self._get_available_analyzer_configs()):
            if config_name in selected_config_set:
                self.analyzer_config_listbox.selection_set(index)

        self.analyzer_group_listbox.selection_clear(0, tk.END)
        for index, group_name in enumerate(self._get_available_analyzer_groups()):
            if group_name in selected_group_set:
                self.analyzer_group_listbox.selection_set(index)

        self._update_analyzer_filter_preview()

    def _build_analyzer_runtime_config(self, require_selection: bool = False) -> dict | None:
        base_config = self._load_analyzer_base_config()
        available_configs = self._get_available_analyzer_configs()
        available_groups = self._get_available_analyzer_groups()
        selected_configs = self._get_selected_analyzer_configs()
        selected_groups = self._get_selected_analyzer_groups()

        if require_selection and len(available_configs) > 0 and len(selected_configs) == 0:
            messagebox.showerror(
                "Input error",
                "Select at least one test/config for the analyzer, or use 'Select all'.")
            return None
        if require_selection and len(available_groups) > 0 and len(selected_groups) == 0:
            messagebox.showerror(
                "Input error",
                "Select at least one instance group for the analyzer, or use 'Select all'.")
            return None

        effective_configs = ['all'] if len(available_configs) == 0 or len(selected_configs) == len(available_configs) else selected_configs
        effective_groups = ['all'] if len(available_groups) == 0 or len(selected_groups) == len(available_groups) else selected_groups

        base_config.pop('analysis_output_subdir', None)
        base_config['configs_to_do'] = effective_configs
        base_config['groups_to_do'] = effective_groups
        base_config.setdefault('instances_to_do', ['all'])
        return base_config

    def _refresh_analyzer_filter_candidates(
            self,
            selected_configs: list[str] | None = None,
            selected_groups: list[str] | None = None):
        available_configs, available_groups = self._scan_analyzer_available_filters()

        if selected_configs is None:
            selected_configs = self._get_selected_analyzer_configs()
        if selected_groups is None:
            selected_groups = self._get_selected_analyzer_groups()

        ordered_configs = self._order_result_plot_config_names(
            list({*available_configs, *[str(name) for name in selected_configs]}),
            self.analyzer_editor.config_data if hasattr(self, "analyzer_editor") else None)
        ordered_groups = sorted(
            {*(str(name) for name in available_groups), *(str(name) for name in selected_groups)},
            key=self._analyzer_group_sort_key)

        self.analyzer_config_listbox.delete(0, tk.END)
        for config_name in ordered_configs:
            self.analyzer_config_listbox.insert(tk.END, config_name)

        self.analyzer_group_listbox.delete(0, tk.END)
        for group_name in ordered_groups:
            self.analyzer_group_listbox.insert(tk.END, group_name)

        if len(ordered_configs) > 0 and len(selected_configs) == 0:
            selected_configs = ordered_configs
        if len(ordered_groups) > 0 and len(selected_groups) == 0:
            selected_groups = ordered_groups

        self._set_selected_analyzer_filters(
            [str(name) for name in selected_configs],
            [str(name) for name in selected_groups])

    def _select_all_analyzer_configs(self):
        self.analyzer_config_listbox.selection_set(0, tk.END)
        self._update_analyzer_filter_preview()

    def _clear_analyzer_configs(self):
        self.analyzer_config_listbox.selection_clear(0, tk.END)
        self._update_analyzer_filter_preview()

    def _select_all_analyzer_groups(self):
        self.analyzer_group_listbox.selection_set(0, tk.END)
        self._update_analyzer_filter_preview()

    def _clear_analyzer_groups(self):
        self.analyzer_group_listbox.selection_clear(0, tk.END)
        self._update_analyzer_filter_preview()

    def _update_analyzer_filter_preview(self, _event=None):
        input_root = self.app.resolve_path(self.analyzer_input_var.get())
        available_configs = self._get_available_analyzer_configs()
        available_groups = self._get_available_analyzer_groups()
        selected_configs = self._get_selected_analyzer_configs()
        selected_groups = self._get_selected_analyzer_groups()

        if len(available_configs) == 0 and len(available_groups) == 0:
            self.analyzer_filter_summary_var.set(
                f"No experiment results detected in {input_root}")
            return

        if (len(available_configs) > 0 and len(selected_configs) == 0) or (len(available_groups) > 0 and len(selected_groups) == 0):
            self.analyzer_filter_summary_var.set(
                "No analyzer filter selected. Use 'Select all' to keep updating the centralized analysis.")
            return

        runtime_config = self._build_analyzer_runtime_config(require_selection=False)
        if runtime_config is None:
            self.analyzer_filter_summary_var.set("Analyzer filters unavailable.")
            return

        target_analysis_path = get_analysis_output_path(input_root, runtime_config)
        selected_config_count = len(selected_configs) if len(available_configs) > 0 else 0
        selected_group_count = len(selected_groups) if len(available_groups) > 0 else 0
        default_global_path = input_root.joinpath("analysis")

        if (
                len(selected_configs) == len(available_configs)
                and len(selected_groups) == len(available_groups)
                and target_analysis_path == default_global_path):
            self.analyzer_filter_summary_var.set(
                f"Central analysis update on all tests/groups -> {target_analysis_path}")
            return

        self.analyzer_filter_summary_var.set(
            f"Central analysis update on {selected_config_count} tests and {selected_group_count} groups -> {target_analysis_path}")

    def _load_analyzer_selection(self):
        if not self.analyzer_editor.load_file():
            return
        config = self.analyzer_editor.config_data

        raw_configs = config.get("configs_to_do")
        selected_configs = []
        if isinstance(raw_configs, list):
            normalized = [str(name).strip() for name in raw_configs if str(name).strip() != ""]
            if len(normalized) > 0 and "all" not in {name.lower() for name in normalized}:
                selected_configs = normalized

        raw_groups = config.get("groups_to_do")
        selected_groups = []
        if isinstance(raw_groups, list):
            normalized = [str(name).strip() for name in raw_groups if str(name).strip() != ""]
            if len(normalized) > 0 and "all" not in {name.lower() for name in normalized}:
                selected_groups = normalized

        self._refresh_analyzer_filter_candidates(selected_configs=selected_configs, selected_groups=selected_groups)

    def _apply_analyzer_selection(self) -> bool:
        target_path = self.app.resolve_path(self.analyzer_config_var.get())
        if not target_path.exists():
            messagebox.showerror("File not found", f"Cannot find config file:\n{target_path}")
            return False

        runtime_config = self._build_analyzer_runtime_config(require_selection=True)
        if runtime_config is None:
            return False

        try:
            with open(target_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(runtime_config, fh, sort_keys=False, allow_unicode=False)
        except Exception as exc:
            messagebox.showerror("Save error", f"Failed to save file:\n{exc}")
            return False

        self.analyzer_editor.load_file()
        self.app.set_status("Updated analyzer filter selection in YAML.")
        return True

    def _build_analyzer_tab(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        self.analyzer_config_var = tk.StringVar(value="configs/analyzer_config.yaml")
        self.analyzer_input_var = tk.StringVar(value="results")
        self.analyzer_overwrite_var = tk.BooleanVar(value=False)
        self.analyzer_filter_summary_var = tk.StringVar(value="")

        menu_tabs = ttk.Notebook(parent)
        menu_tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 10))

        run_tab = ttk.Frame(menu_tabs)
        run_tab.columnconfigure(0, weight=1)
        menu_tabs.add(run_tab, text="Run")

        filter_tab = ttk.Frame(menu_tabs)
        filter_tab.columnconfigure(0, weight=1)
        menu_tabs.add(filter_tab, text="Batch filters")

        advanced_tab = ttk.Frame(menu_tabs)
        advanced_tab.columnconfigure(0, weight=1)
        advanced_tab.rowconfigure(0, weight=1)
        menu_tabs.add(advanced_tab, text="Advanced YAML")

        controls = ttk.LabelFrame(run_tab, text="Run Analyzer")
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Config file").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.analyzer_config_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=lambda: self.app.browse_file(
            self.analyzer_config_var,
            [("YAML files", "*.yaml *.yml"), ("All files", "*.*")])).grid(row=0, column=2, padx=6, pady=4)

        ttk.Label(controls, text="Results input").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.analyzer_input_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=lambda: self.app.browse_directory(self.analyzer_input_var)).grid(
            row=1, column=2, padx=6, pady=4)

        ttk.Checkbutton(
            controls,
            text="Force full rebuild (disable incremental reuse)",
            variable=self.analyzer_overwrite_var).grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=6)

        ttk.Label(
            controls,
            text="By default the analyzer reuses unchanged config/group/instance blocks from the centralized analysis store and recalculates only changed or new results.",
            foreground="#555",
            wraplength=760,
            justify="left").grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6))

        actions = ttk.Frame(controls)
        actions.grid(row=3, column=2, sticky="e", padx=8, pady=6)
        ttk.Button(actions, text="Run analyzer", command=self._run_analyzer).pack(side="left")
        ttk.Button(actions, text="Open analysis folder", command=self._open_analysis_folder).pack(side="left", padx=(6, 0))

        filter_controls = ttk.LabelFrame(filter_tab, text="Analyzer Selection")
        filter_controls.grid(row=0, column=0, sticky="nsew")
        filter_controls.columnconfigure(0, weight=1)
        filter_controls.columnconfigure(1, weight=1)
        filter_controls.rowconfigure(2, weight=1)

        ttk.Label(
            filter_controls,
            text="Select which tests and instance groups to refresh inside the centralized analysis under results/analysis. A narrow selection updates only that subset and preserves the rest of the canonical analysis tables.",
            wraplength=860,
            justify="left").grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 4))

        top_actions = ttk.Frame(filter_controls)
        top_actions.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))
        ttk.Button(top_actions, text="Refresh from results", command=self._refresh_analyzer_filter_candidates).pack(side="left")

        config_frame = ttk.LabelFrame(filter_controls, text="Tests / configs")
        config_frame.grid(row=2, column=0, sticky="nsew", padx=(8, 4), pady=(0, 4))
        config_frame.columnconfigure(0, weight=1)
        config_frame.rowconfigure(1, weight=1)
        config_actions = ttk.Frame(config_frame)
        config_actions.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        ttk.Button(config_actions, text="Select all", command=self._select_all_analyzer_configs).pack(side="left")
        ttk.Button(config_actions, text="Clear", command=self._clear_analyzer_configs).pack(side="left", padx=(6, 0))
        config_list_frame = ttk.Frame(config_frame)
        config_list_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        config_list_frame.columnconfigure(0, weight=1)
        config_list_frame.rowconfigure(0, weight=1)
        self.analyzer_config_listbox = tk.Listbox(
            config_list_frame,
            selectmode="extended",
            exportselection=False,
            height=10)
        self.analyzer_config_listbox.grid(row=0, column=0, sticky="nsew")
        self.analyzer_config_listbox.bind("<<ListboxSelect>>", self._update_analyzer_filter_preview)
        config_scroll = ttk.Scrollbar(config_list_frame, orient="vertical", command=self.analyzer_config_listbox.yview)
        config_scroll.grid(row=0, column=1, sticky="ns")
        self.analyzer_config_listbox.configure(yscrollcommand=config_scroll.set)

        group_frame = ttk.LabelFrame(filter_controls, text="Instance groups")
        group_frame.grid(row=2, column=1, sticky="nsew", padx=(4, 8), pady=(0, 4))
        group_frame.columnconfigure(0, weight=1)
        group_frame.rowconfigure(1, weight=1)
        group_actions = ttk.Frame(group_frame)
        group_actions.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        ttk.Button(group_actions, text="Select all", command=self._select_all_analyzer_groups).pack(side="left")
        ttk.Button(group_actions, text="Clear", command=self._clear_analyzer_groups).pack(side="left", padx=(6, 0))
        group_list_frame = ttk.Frame(group_frame)
        group_list_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        group_list_frame.columnconfigure(0, weight=1)
        group_list_frame.rowconfigure(0, weight=1)
        self.analyzer_group_listbox = tk.Listbox(
            group_list_frame,
            selectmode="extended",
            exportselection=False,
            height=10)
        self.analyzer_group_listbox.grid(row=0, column=0, sticky="nsew")
        self.analyzer_group_listbox.bind("<<ListboxSelect>>", self._update_analyzer_filter_preview)
        group_scroll = ttk.Scrollbar(group_list_frame, orient="vertical", command=self.analyzer_group_listbox.yview)
        group_scroll.grid(row=0, column=1, sticky="ns")
        self.analyzer_group_listbox.configure(yscrollcommand=group_scroll.set)

        ttk.Label(
            filter_controls,
            textvariable=self.analyzer_filter_summary_var,
            foreground="#555",
            wraplength=860,
            justify="left").grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))

        self.analyzer_editor = ConfigEditor(
            advanced_tab,
            project_root=self.app.project_root,
            path_var=self.analyzer_config_var,
            status_cb=self.app.set_status)
        self.analyzer_editor.grid(row=0, column=0, sticky="nsew")

        self.analyzer_input_var.trace_add("write", lambda *_args: self._refresh_analyzer_filter_candidates())
        self.after(0, self._load_analyzer_selection)

    def _persist_plot_selection(
            self,
            path_var: tk.StringVar,
            editor: ConfigEditor,
            selection_panel: PlotSelectionPanel,
            extra_updates: dict | None = None,
            status_message: str = "Updated plot configuration.") -> bool:
        target_path = self.app.resolve_path(path_var.get())
        path = target_path
        if not path.exists():
            messagebox.showerror("File not found", f"Cannot find config file:\n{path}")
            return False

        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
        except Exception as exc:
            messagebox.showerror("YAML error", f"Failed to read YAML:\n{exc}")
            return False

        if not isinstance(loaded, dict):
            messagebox.showerror("Unsupported format", "Top-level YAML content must be a mapping/object.")
            return False

        loaded["plots_to_do"] = selection_panel.get_selected()
        if extra_updates is not None:
            for key, value in extra_updates.items():
                if value is DELETE_FIELD:
                    loaded.pop(key, None)
                else:
                    loaded[key] = value

        try:
            with open(path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(loaded, fh, sort_keys=False, allow_unicode=False)
        except Exception as exc:
            messagebox.showerror("Save error", f"Failed to save file:\n{exc}")
            return False

        editor.load_file()
        self.app.set_status(status_message)
        return True

    def _persist_result_plot_selection(
            self,
            path_var: tk.StringVar,
            editor: ConfigEditor,
            selection_panel: ResultPlotSelectionPanel,
            extra_updates: dict | None = None,
            status_message: str = "Updated result plot configuration.") -> bool:
        target_path = self.app.resolve_path(path_var.get())
        if not target_path.exists():
            messagebox.showerror("File not found", f"Cannot find config file:\n{target_path}")
            return False

        try:
            with open(target_path, "r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
        except Exception as exc:
            messagebox.showerror("YAML error", f"Failed to read YAML:\n{exc}")
            return False

        if not isinstance(loaded, dict):
            messagebox.showerror("Unsupported format", "Top-level YAML content must be a mapping/object.")
            return False

        loaded["plots_to_do"] = serialize_result_plots_to_do(selection_panel.get_selected_map())
        if extra_updates is not None:
            for key, value in extra_updates.items():
                if value is DELETE_FIELD:
                    loaded.pop(key, None)
                else:
                    loaded[key] = value

        try:
            with open(target_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(loaded, fh, sort_keys=False, allow_unicode=False)
        except Exception as exc:
            messagebox.showerror("Save error", f"Failed to save file:\n{exc}")
            return False

        editor.load_file()
        self.app.set_status(status_message)
        return True

    def _collect_result_plot_extra_updates(self) -> dict | None:
        updates: dict[str, object] = {}
        available_configs = self._get_result_plot_comparison_available_configs()
        selected_configs = self._get_selected_result_plot_comparison_configs()
        selected_result_plots = self.result_plot_selection.get_selected_map()
        comparison_plots_selected = len(selected_result_plots.get('comparison', [])) > 0
        row_split_priority = self._get_result_plot_row_split_priority()

        if comparison_plots_selected and len(available_configs) > 0 and len(selected_configs) == 0:
            messagebox.showerror(
                "Input error",
                "Select at least one test/config to compare, or use 'Select all' to disable the filter.")
            return None

        if comparison_plots_selected:
            if len(available_configs) == 0 or len(selected_configs) == len(available_configs):
                updates["experiment_group_comparison_configs_to_do"] = DELETE_FIELD
                updates["experiment_group_comparison_output_subdir"] = DELETE_FIELD
            else:
                updates["experiment_group_comparison_configs_to_do"] = selected_configs
                updates["experiment_group_comparison_output_subdir"] = (
                    f"comparisons/{self._build_result_plot_comparison_slug(selected_configs)}"
                )

            if len(row_split_priority) == 0:
                updates["experiment_group_comparison_row_split_priority"] = DELETE_FIELD
            else:
                updates["experiment_group_comparison_row_split_priority"] = row_split_priority

        selected_run_config = self.result_plot_run_config_var.get().strip()
        selected_run_group = self.result_plot_run_group_var.get().strip()
        selected_run_instance = self.result_plot_run_instance_var.get().strip()

        updates["run_plot_configs_to_do"] = (
            DELETE_FIELD if selected_run_config in {"", "All"} else [selected_run_config]
        )
        updates["run_plot_groups_to_do"] = (
            DELETE_FIELD if selected_run_group in {"", "All"} else [selected_run_group]
        )
        updates["run_plot_instances_to_do"] = (
            DELETE_FIELD if selected_run_instance in {"", "All"} else [selected_run_instance]
        )
        return updates

    def _get_result_plot_row_split_priority(self) -> list[str]:
        ordered_values = [
            self.result_plot_row_split_var_1.get(),
            self.result_plot_row_split_var_2.get(),
            self.result_plot_row_split_var_3.get(),
        ]
        resolved: list[str] = []
        for label in ordered_values:
            normalized = EXPERIMENT_COMPARISON_ROW_SPLIT_LABEL_TO_KEY.get(str(label), "")
            if normalized == "" or normalized in resolved:
                continue
            resolved.append(normalized)
        return resolved

    def _set_result_plot_row_split_priority(self, priority_values: list[str]):
        labels = [
            EXPERIMENT_COMPARISON_ROW_SPLIT_KEY_TO_LABEL.get(value, "None")
            for value in priority_values
        ]
        labels += ["None"] * max(0, 3 - len(labels))
        self.result_plot_row_split_var_1.set(labels[0])
        self.result_plot_row_split_var_2.set(labels[1])
        self.result_plot_row_split_var_3.set(labels[2])
        self._update_result_plot_comparison_preview()

    def _order_result_plot_config_names(self, config_names: list[str], config: dict | None = None) -> list[str]:
        names = sorted({str(name) for name in config_names if str(name).strip() != ""})
        if len(names) == 0:
            return []

        configured_order: list[str] = []
        if isinstance(config, dict):
            raw_order = config.get("experiment_group_comparison_config_order")
            if isinstance(raw_order, list):
                configured_order = [str(name) for name in raw_order if str(name).strip() != ""]

        if len(configured_order) == 0:
            return names

        rank = {name: index for index, name in enumerate(configured_order)}
        return sorted(names, key=lambda name: (rank.get(name, len(rank)), name))

    def _scan_result_plot_available_configs(self) -> list[str]:
        input_root = self.app.resolve_path(self.result_plotter_input_var.get())
        if not input_root.exists():
            return []

        config_names: set[str] = set()
        for entry in sorted(input_root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in {"analysis", "plots"}:
                continue
            tokens = entry.name.split("__")
            if len(tokens) != 3:
                continue
            config_names.add(tokens[0])

        return self._order_result_plot_config_names(
            list(config_names),
            self.result_plotter_editor.config_data if hasattr(self, "result_plotter_editor") else None)

    def _get_result_plot_comparison_available_configs(self) -> list[str]:
        return [self.result_plot_comparison_listbox.get(index) for index in range(self.result_plot_comparison_listbox.size())]

    def _get_selected_result_plot_comparison_configs(self) -> list[str]:
        return [self.result_plot_comparison_listbox.get(index) for index in self.result_plot_comparison_listbox.curselection()]

    def _set_selected_result_plot_comparison_configs(self, selected_configs: list[str]):
        selected_set = {str(name) for name in selected_configs}
        self.result_plot_comparison_listbox.selection_clear(0, tk.END)
        for index, config_name in enumerate(self._get_result_plot_comparison_available_configs()):
            if config_name in selected_set:
                self.result_plot_comparison_listbox.selection_set(index)
        self._update_result_plot_comparison_preview()

    def _build_result_plot_comparison_slug(self, selected_configs: list[str]) -> str:
        normalized = [str(name).strip() for name in selected_configs if str(name).strip() != ""]
        if len(normalized) == 0:
            return "all_configs"
        return "__".join(
            re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "config"
            for name in normalized)

    def _refresh_result_plot_comparison_candidates(self, selected_configs: list[str] | None = None):
        available_configs = self._scan_result_plot_available_configs()
        if selected_configs is None:
            selected_configs = self._get_selected_result_plot_comparison_configs()

        ordered_names = self._order_result_plot_config_names(
            list({*available_configs, *[str(name) for name in selected_configs]}),
            self.result_plotter_editor.config_data if hasattr(self, "result_plotter_editor") else None)

        self.result_plot_comparison_listbox.delete(0, tk.END)
        for config_name in ordered_names:
            self.result_plot_comparison_listbox.insert(tk.END, config_name)

        if len(ordered_names) > 0:
            normalized_selected = [str(name) for name in selected_configs]
            if len(normalized_selected) == 0:
                normalized_selected = ordered_names
            self._set_selected_result_plot_comparison_configs(normalized_selected)
        else:
            self._update_result_plot_comparison_preview()

    def _scan_result_plot_run_entries(self) -> list[tuple[str, str, str]]:
        input_root = self.app.resolve_path(self.result_plotter_input_var.get())
        if not input_root.exists():
            return []

        entries: list[tuple[str, str, str]] = []
        for entry in sorted(input_root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in {"analysis", "plots"}:
                continue
            tokens = entry.name.split("__")
            if len(tokens) != 3:
                continue
            entries.append((tokens[0], tokens[1], tokens[2]))
        return entries

    def _refresh_result_plot_run_candidates(
            self,
            selected_config: str | None = None,
            selected_group: str | None = None,
            selected_instance: str | None = None):
        entries = self._scan_result_plot_run_entries()
        config_names = sorted({config_name for config_name, _, _ in entries})

        config_values = ["All"] + config_names
        self.result_plot_run_config_combo["values"] = config_values
        if selected_config is None:
            selected_config = self.result_plot_run_config_var.get().strip() or "All"
        if selected_config not in config_values:
            selected_config = "All"
        self.result_plot_run_config_var.set(selected_config)

        filtered_by_config = [
            entry for entry in entries
            if selected_config == "All" or entry[0] == selected_config
        ]
        group_names = sorted({group_name for _, group_name, _ in filtered_by_config})
        group_values = ["All"] + group_names
        self.result_plot_run_group_combo["values"] = group_values
        if selected_group is None:
            selected_group = self.result_plot_run_group_var.get().strip() or "All"
        if selected_group not in group_values:
            selected_group = "All"
        self.result_plot_run_group_var.set(selected_group)

        filtered_by_group = [
            entry for entry in filtered_by_config
            if selected_group == "All" or entry[1] == selected_group
        ]
        instance_names = sorted(
            {instance_name for _, _, instance_name in filtered_by_group},
            key=self._result_plot_instance_sort_key)
        instance_values = ["All"] + instance_names
        self.result_plot_run_instance_combo["values"] = instance_values
        if selected_instance is None:
            selected_instance = self.result_plot_run_instance_var.get().strip() or "All"
        if selected_instance not in instance_values:
            selected_instance = "All"
        self.result_plot_run_instance_var.set(selected_instance)

        self._update_result_plot_run_preview()

    def _on_result_plot_run_config_change(self, _event=None):
        self._refresh_result_plot_run_candidates(
            selected_config=self.result_plot_run_config_var.get(),
            selected_group="All",
            selected_instance="All")

    def _on_result_plot_run_group_change(self, _event=None):
        self._refresh_result_plot_run_candidates(
            selected_config=self.result_plot_run_config_var.get(),
            selected_group=self.result_plot_run_group_var.get(),
            selected_instance="All")

    def _update_result_plot_run_preview(self, _event=None):
        input_root = self.app.resolve_path(self.result_plotter_input_var.get())
        entries = self._scan_result_plot_run_entries()
        if len(entries) == 0:
            self.result_plot_run_summary_var.set(f"No result runs detected in {input_root}")
            return

        selected_config = self.result_plot_run_config_var.get().strip() or "All"
        selected_group = self.result_plot_run_group_var.get().strip() or "All"
        selected_instance = self.result_plot_run_instance_var.get().strip() or "All"

        filtered_entries = [
            entry for entry in entries
            if (selected_config == "All" or entry[0] == selected_config)
            and (selected_group == "All" or entry[1] == selected_group)
            and (selected_instance == "All" or entry[2] == selected_instance)
        ]

        if selected_config == "All" and selected_group == "All" and selected_instance == "All":
            self.result_plot_run_summary_var.set(
                "No run-specific filter set -> run-level plots use all runs allowed by the global YAML filters."
            )
            return

        self.result_plot_run_summary_var.set(
            f"Run-level plots restricted to {len(filtered_entries)} result directories "
            f"(config={selected_config}, group={selected_group}, instance={selected_instance})."
        )

    def _select_all_result_plot_comparison_configs(self):
        self.result_plot_comparison_listbox.selection_set(0, tk.END)
        self._update_result_plot_comparison_preview()

    def _clear_result_plot_comparison_configs(self):
        self.result_plot_comparison_listbox.selection_clear(0, tk.END)
        self._update_result_plot_comparison_preview()

    def _update_result_plot_comparison_preview(self, _event=None):
        input_root = self.app.resolve_path(self.result_plotter_input_var.get())
        base_plots_path = input_root.joinpath("plots")
        available_configs = self._get_result_plot_comparison_available_configs()
        selected_configs = self._get_selected_result_plot_comparison_configs()
        row_split_priority = self._get_result_plot_row_split_priority()
        pretty_row_split = {
            "patient_number": "patients",
            "care_unit_number": "care units",
            "test": "test",
        }
        row_split_preview = (
            "single row"
            if len(row_split_priority) == 0
            else " > ".join(pretty_row_split.get(value, value) for value in row_split_priority)
        )

        if len(available_configs) == 0:
            self.result_plot_comparison_summary_var.set(
                f"No result configs detected in {input_root}")
            return

        if len(selected_configs) == 0:
            self.result_plot_comparison_summary_var.set(
                "No test selected. Select at least one config, or use 'Select all'.")
            return

        if len(selected_configs) == len(available_configs):
            self.result_plot_comparison_summary_var.set(
                f"All tests selected -> aggregate comparison plots stay in {base_plots_path} | row split: {row_split_preview}")
            return

        target_path = base_plots_path.joinpath(
            "comparisons",
            self._build_result_plot_comparison_slug(selected_configs))
        self.result_plot_comparison_summary_var.set(
            f"Filtered comparison on {len(selected_configs)} tests -> {target_path} | row split: {row_split_preview}")

    def _load_result_plot_extras_from_config(self, config: dict):
        raw_configs = config.get("experiment_group_comparison_configs_to_do")
        selected_configs = []
        if isinstance(raw_configs, list):
            normalized = [str(name).strip() for name in raw_configs if str(name).strip() != ""]
            if len(normalized) > 0 and "all" not in {name.lower() for name in normalized}:
                selected_configs = normalized
        raw_row_split_priority = config.get("experiment_group_comparison_row_split_priority")
        row_split_priority = []
        if isinstance(raw_row_split_priority, list):
            row_split_priority = [str(value).strip() for value in raw_row_split_priority if str(value).strip() != ""]
        self._set_result_plot_row_split_priority(row_split_priority)
        self._refresh_result_plot_comparison_candidates(selected_configs=selected_configs)

        raw_run_configs = config.get("run_plot_configs_to_do")
        selected_run_config = "All"
        if isinstance(raw_run_configs, list):
            normalized = [str(value).strip() for value in raw_run_configs if str(value).strip() != ""]
            if len(normalized) > 0 and "all" not in {value.lower() for value in normalized}:
                selected_run_config = normalized[0]

        raw_run_groups = config.get("run_plot_groups_to_do")
        selected_run_group = "All"
        if isinstance(raw_run_groups, list):
            normalized = [str(value).strip() for value in raw_run_groups if str(value).strip() != ""]
            if len(normalized) > 0 and "all" not in {value.lower() for value in normalized}:
                selected_run_group = normalized[0]

        raw_run_instances = config.get("run_plot_instances_to_do")
        selected_run_instance = "All"
        if isinstance(raw_run_instances, list):
            normalized = [str(value).strip() for value in raw_run_instances if str(value).strip() != ""]
            if len(normalized) > 0 and "all" not in {value.lower() for value in normalized}:
                selected_run_instance = normalized[0]

        self._refresh_result_plot_run_candidates(
            selected_config=selected_run_config,
            selected_group=selected_run_group,
            selected_instance=selected_run_instance)

    def _load_result_plot_selection(self):
        if not self.result_plotter_editor.load_file():
            return
        config = self.result_plotter_editor.config_data
        self.result_plot_selection.set_selected_map(parse_result_plots_to_do(config.get("plots_to_do", {})))
        self._load_result_plot_extras_from_config(config)

    def _apply_result_plot_selection(self) -> bool:
        target_path = self.app.resolve_path(self.result_plotter_config_var.get())
        if self.result_plotter_editor.current_file != target_path:
            if not self.result_plotter_editor.load_file():
                return False
            config = self.result_plotter_editor.config_data
            self.result_plot_selection.set_selected_map(parse_result_plots_to_do(config.get("plots_to_do", {})))
            self._load_result_plot_extras_from_config(config)

        updates = self._collect_result_plot_extra_updates()
        if updates is None:
            return False
        return self._persist_result_plot_selection(
            self.result_plotter_config_var,
            self.result_plotter_editor,
            self.result_plot_selection,
            extra_updates=updates,
            status_message="Updated result plot selection in YAML.")

    def _load_instance_plot_selection(self):
        if not self.master_plotter_editor.load_file():
            return
        config = self.master_plotter_editor.config_data
        plots_to_do = config.get("plots_to_do", [])
        self.master_plot_selection.set_selected(plots_to_do if isinstance(plots_to_do, list) else [])
        self.master_plot_skip_existing_var.set(bool(config.get("skip_existing", False)))
        self.master_plot_global_scales_var.set(bool(config.get("use_global_value_scales", True)))

    def _apply_instance_plot_selection(self) -> bool:
        return self._persist_plot_selection(
            self.master_plotter_config_var,
            self.master_plotter_editor,
            self.master_plot_selection,
            extra_updates={
                "skip_existing": self.master_plot_skip_existing_var.get(),
                "use_global_value_scales": self.master_plot_global_scales_var.get(),
            },
            status_message="Updated master-instance plot selection in YAML.")

    def _build_result_plotter_tab(self, parent):
        parent.rowconfigure(0, weight=1)
        self.result_plotter_config_var = tk.StringVar(value="configs/plotter_config.yaml")
        self.result_plotter_input_var = tk.StringVar(value="results")
        self.plot_instance_input_var = tk.StringVar(value="")
        self.plot_instance_output_var = tk.StringVar(value="plots_single")
        self.plot_instance_iter_var = tk.IntVar(value=1)
        self.result_plot_comparison_summary_var = tk.StringVar(value="")
        self.result_plot_row_split_var_1 = tk.StringVar(value="None")
        self.result_plot_row_split_var_2 = tk.StringVar(value="None")
        self.result_plot_row_split_var_3 = tk.StringVar(value="None")
        self.result_plot_run_config_var = tk.StringVar(value="All")
        self.result_plot_run_group_var = tk.StringVar(value="All")
        self.result_plot_run_instance_var = tk.StringVar(value="All")
        self.result_plot_run_summary_var = tk.StringVar(value="")

        menu_tabs = ttk.Notebook(parent)
        menu_tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 10))

        batch_tab = ttk.Frame(menu_tabs)
        batch_tab.columnconfigure(0, weight=1)
        menu_tabs.add(batch_tab, text="Batch")

        single_tab = ttk.Frame(menu_tabs)
        single_tab.columnconfigure(0, weight=1)
        menu_tabs.add(single_tab, text="Single iteration snapshot")

        advanced_tab = ttk.Frame(menu_tabs)
        advanced_tab.columnconfigure(0, weight=1)
        advanced_tab.rowconfigure(0, weight=1)
        menu_tabs.add(advanced_tab, text="Advanced YAML")

        batch_controls = ttk.LabelFrame(batch_tab, text="Batch Plotter")
        batch_controls.grid(row=0, column=0, sticky="ew")
        batch_controls.columnconfigure(1, weight=1)

        ttk.Label(batch_controls, text="Config file").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(batch_controls, textvariable=self.result_plotter_config_var).grid(
            row=0, column=1, sticky="ew", pady=4)
        ttk.Button(batch_controls, text="Browse", command=lambda: self.app.browse_file(
            self.result_plotter_config_var,
            [("YAML files", "*.yaml *.yml"), ("All files", "*.*")])).grid(row=0, column=2, padx=6, pady=4)

        ttk.Label(batch_controls, text="Results input").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(batch_controls, textvariable=self.result_plotter_input_var).grid(
            row=1, column=1, sticky="ew", pady=4)
        ttk.Button(batch_controls, text="Browse", command=lambda: self.app.browse_directory(self.result_plotter_input_var)).grid(
            row=1, column=2, padx=6, pady=4)

        all_actions = ttk.Frame(batch_controls)
        all_actions.grid(row=2, column=2, sticky="e", padx=8, pady=(6, 2))
        ttk.Button(all_actions, text="Run plotter all", command=self._run_plotter_all).pack(side="left")

        self.result_plot_selection = ResultPlotSelectionPanel(
            batch_controls,
            title="Batch plots to run",
            on_load=self._load_result_plot_selection,
            on_apply=self._apply_result_plot_selection)
        self.result_plot_selection.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))

        comparison_parent = self.result_plot_selection.level_panels['comparison']
        comparison_parent.columnconfigure(1, weight=0, minsize=360)
        comparison_options = ttk.LabelFrame(comparison_parent, text="Experiment Comparison Options")
        comparison_options.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(0, 8), pady=(8, 8))
        comparison_options.columnconfigure(0, weight=1)
        comparison_options.rowconfigure(2, weight=1)

        ttk.Label(
            comparison_options,
            text="Select the tests/configs to include in the experiment comparison plots. "
                 "Order and aliases stay editable only in Advanced YAML.").grid(
                    row=0, column=0, sticky="ew", padx=8, pady=(6, 4))

        comparison_actions = ttk.Frame(comparison_options)
        comparison_actions.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))
        ttk.Button(comparison_actions, text="Refresh from results", command=self._refresh_result_plot_comparison_candidates).pack(side="left")
        ttk.Button(comparison_actions, text="Select all", command=self._select_all_result_plot_comparison_configs).pack(side="left", padx=(6, 0))
        ttk.Button(comparison_actions, text="Clear", command=self._clear_result_plot_comparison_configs).pack(side="left", padx=(6, 0))

        ttk.Label(comparison_options, text="Row split priority").grid(
            row=2, column=0, sticky="w", padx=8, pady=(0, 4))
        split_frame = ttk.Frame(comparison_options)
        split_frame.grid(row=3, column=0, sticky="w", padx=8, pady=(0, 6))
        split_labels = list(EXPERIMENT_COMPARISON_ROW_SPLIT_LABEL_TO_KEY.keys())
        for variable in [
                self.result_plot_row_split_var_1,
                self.result_plot_row_split_var_2,
                self.result_plot_row_split_var_3]:
            combo = ttk.Combobox(
                split_frame,
                state="readonly",
                width=12,
                values=split_labels,
                textvariable=variable)
            combo.pack(side="left", padx=(0, 4))
            combo.bind("<<ComboboxSelected>>", self._update_result_plot_comparison_preview)

        comparison_list_frame = ttk.Frame(comparison_options)
        comparison_list_frame.grid(row=4, column=0, sticky="nsew", padx=8, pady=(0, 4))
        comparison_list_frame.columnconfigure(0, weight=1)
        comparison_list_frame.rowconfigure(0, weight=1)
        self.result_plot_comparison_listbox = tk.Listbox(
            comparison_list_frame,
            selectmode="extended",
            exportselection=False,
            height=10)
        self.result_plot_comparison_listbox.grid(row=0, column=0, sticky="nsew")
        self.result_plot_comparison_listbox.bind("<<ListboxSelect>>", self._update_result_plot_comparison_preview)
        comparison_scroll = ttk.Scrollbar(comparison_list_frame, orient="vertical", command=self.result_plot_comparison_listbox.yview)
        comparison_scroll.grid(row=0, column=1, sticky="ns")
        self.result_plot_comparison_listbox.configure(yscrollcommand=comparison_scroll.set)

        ttk.Label(
            comparison_options,
            textvariable=self.result_plot_comparison_summary_var,
            foreground="#555",
            wraplength=340,
            justify="left").grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 8))

        run_parent = self.result_plot_selection.level_panels['run']
        run_parent.columnconfigure(1, weight=0, minsize=320)
        run_options = ttk.LabelFrame(run_parent, text="Run Plot Filters")
        run_options.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(0, 8), pady=(8, 8))
        run_options.columnconfigure(0, weight=1)

        ttk.Label(
            run_options,
            text="Optional selector for run-level plots only. Leave all fields on 'All' to process every run allowed by the global YAML filters."
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))

        ttk.Button(
            run_options,
            text="Refresh from results",
            command=self._refresh_result_plot_run_candidates).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))

        ttk.Label(run_options, text="Config").grid(row=2, column=0, sticky="w", padx=8, pady=(2, 2))
        self.result_plot_run_config_combo = ttk.Combobox(
            run_options,
            textvariable=self.result_plot_run_config_var,
            state="readonly",
            width=28)
        self.result_plot_run_config_combo.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 6))
        self.result_plot_run_config_combo.bind("<<ComboboxSelected>>", self._on_result_plot_run_config_change)

        ttk.Label(run_options, text="Group").grid(row=4, column=0, sticky="w", padx=8, pady=(2, 2))
        self.result_plot_run_group_combo = ttk.Combobox(
            run_options,
            textvariable=self.result_plot_run_group_var,
            state="readonly",
            width=28)
        self.result_plot_run_group_combo.grid(row=5, column=0, sticky="ew", padx=8, pady=(0, 6))
        self.result_plot_run_group_combo.bind("<<ComboboxSelected>>", self._on_result_plot_run_group_change)

        ttk.Label(run_options, text="Instance").grid(row=6, column=0, sticky="w", padx=8, pady=(2, 2))
        self.result_plot_run_instance_combo = ttk.Combobox(
            run_options,
            textvariable=self.result_plot_run_instance_var,
            state="readonly",
            width=28)
        self.result_plot_run_instance_combo.grid(row=7, column=0, sticky="ew", padx=8, pady=(0, 6))
        self.result_plot_run_instance_combo.bind("<<ComboboxSelected>>", self._update_result_plot_run_preview)

        ttk.Label(
            run_options,
            textvariable=self.result_plot_run_summary_var,
            foreground="#555",
            wraplength=300,
            justify="left").grid(row=8, column=0, sticky="ew", padx=8, pady=(2, 8))

        single_controls = ttk.LabelFrame(single_tab, text="Single-iteration Snapshot Plotter")
        single_controls.grid(row=0, column=0, sticky="ew")
        single_controls.columnconfigure(1, weight=1)

        ttk.Label(single_controls, text="Single result dir").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(single_controls, textvariable=self.plot_instance_input_var).grid(
            row=0, column=1, sticky="ew", pady=4)
        ttk.Button(single_controls, text="Browse", command=lambda: self.app.browse_directory(
            self.plot_instance_input_var)).grid(row=0, column=2, padx=6, pady=4)

        ttk.Label(single_controls, text="Single output dir").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(single_controls, textvariable=self.plot_instance_output_var).grid(
            row=1, column=1, sticky="ew", pady=4)
        ttk.Button(single_controls, text="Browse", command=lambda: self.app.browse_directory(
            self.plot_instance_output_var)).grid(row=1, column=2, padx=6, pady=4)

        ttk.Label(single_controls, text="Iteration").grid(row=2, column=0, sticky="w", padx=8, pady=4)
        ttk.Spinbox(
            single_controls,
            from_=1,
            to=999999,
            textvariable=self.plot_instance_iter_var,
            width=10).grid(row=2, column=1, sticky="w", pady=4)

        inst_actions = ttk.Frame(single_controls)
        inst_actions.grid(row=2, column=2, sticky="e", padx=8, pady=6)
        ttk.Button(inst_actions, text="Run iteration snapshot", command=self._run_plotter_instance).pack(side="left")

        self.result_plotter_editor = ConfigEditor(
            advanced_tab,
            project_root=self.app.project_root,
            path_var=self.result_plotter_config_var,
            status_cb=self.app.set_status)
        self.result_plotter_editor.grid(row=0, column=0, sticky="nsew")
        self.result_plotter_input_var.trace_add("write", lambda *_args: self._refresh_result_plot_comparison_candidates())
        self.result_plotter_input_var.trace_add("write", lambda *_args: self._refresh_result_plot_run_candidates())
        self.after(0, self._load_result_plot_selection)

    def _build_instance_plotter_tab(self, parent):
        controls = ttk.LabelFrame(parent, text="Run Master Instance Plotter")
        controls.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        controls.columnconfigure(1, weight=1)

        self.master_plotter_config_var = tk.StringVar(value="configs/master_instance_plotter_config.yaml")
        self.master_plot_input_var = tk.StringVar(value="instances")
        self.master_plot_skip_existing_var = tk.BooleanVar(value=False)
        self.master_plot_global_scales_var = tk.BooleanVar(value=True)

        ttk.Label(controls, text="Config file").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.master_plotter_config_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=lambda: self.app.browse_file(
            self.master_plotter_config_var,
            [("YAML files", "*.yaml *.yml"), ("All files", "*.*")])).grid(row=0, column=2, padx=6, pady=4)

        ttk.Label(controls, text="Instances input").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(controls, textvariable=self.master_plot_input_var).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=lambda: self.app.browse_directory(self.master_plot_input_var)).grid(
            row=1, column=2, padx=6, pady=4)

        ttk.Checkbutton(
            controls,
            text="Skip existing PNG files",
            variable=self.master_plot_skip_existing_var).grid(row=2, column=0, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(
            controls,
            text="Use global comparable value scales",
            variable=self.master_plot_global_scales_var).grid(row=2, column=1, sticky="w", padx=8, pady=6)

        actions = ttk.Frame(controls)
        actions.grid(row=2, column=2, sticky="e", padx=8, pady=6)
        ttk.Button(actions, text="Run instance plots", command=self._run_master_instance_plotter).pack(side="left")
        ttk.Button(actions, text="Open plots root", command=self._open_master_plot_root).pack(side="left", padx=(6, 0))

        self.master_plot_selection = PlotSelectionPanel(
            controls,
            title="Instance plot selection",
            plot_names=MASTER_INSTANCE_PLOT_NAMES,
            on_load=self._load_instance_plot_selection,
            on_apply=self._apply_instance_plot_selection,
            columns=2)
        self.master_plot_selection.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 6))

        self.master_plotter_editor = ConfigEditor(
            parent,
            project_root=self.app.project_root,
            path_var=self.master_plotter_config_var,
            status_cb=self.app.set_status)
        self.master_plotter_editor.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.after(0, self._load_instance_plot_selection)

    def _run_analyzer(self):
        if not self._apply_analyzer_selection():
            return
        cmd = [
            self.app.runner_python,
            "analyzer.py",
            "-c", str(self.app.resolve_path(self.analyzer_config_var.get())),
            "-i", str(self.app.resolve_path(self.analyzer_input_var.get()))]
        if self.analyzer_overwrite_var.get():
            cmd.append("--overwrite")
        self.app.start_command(cmd)

    def _open_analysis_folder(self):
        runtime_config = self._build_analyzer_runtime_config(require_selection=False)
        analysis_dir = get_analysis_output_path(
            self.app.resolve_path(self.analyzer_input_var.get()),
            runtime_config or {})
        self.app.open_path(analysis_dir)

    def _run_plotter_all(self):
        if not self._apply_result_plot_selection():
            return
        cmd = [
            self.app.runner_python,
            "plotter.py",
            "all",
            "-c", str(self.app.resolve_path(self.result_plotter_config_var.get())),
            "-i", str(self.app.resolve_path(self.result_plotter_input_var.get()))]
        self.app.start_command(cmd)

    def _run_plotter_instance(self):
        cmd = [
            self.app.runner_python,
            "plotter.py",
            "instance",
            "-i", str(self.app.resolve_path(self.plot_instance_input_var.get())),
            "-o", str(self.app.resolve_path(self.plot_instance_output_var.get())),
            "--iter", str(self.plot_instance_iter_var.get())]
        self.app.start_command(cmd)

    def _run_master_instance_plotter(self):
        if not self._apply_instance_plot_selection():
            return
        cmd = [
            self.app.runner_python,
            "master_instance_plotter.py",
            "-c", str(self.app.resolve_path(self.master_plotter_config_var.get())),
            "-i", str(self.app.resolve_path(self.master_plot_input_var.get()))]
        self.app.start_command(cmd)

    def _open_master_plot_root(self):
        plots_root = self.app.resolve_path(self.master_plot_input_var.get()).joinpath("plots_instances")
        self.app.open_path(plots_root)


class ControlPanelApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.project_root = Path(__file__).resolve().parent
        runner_python = os.getenv("SCHEDULER_GUI_RUNNER_PYTHON", "").strip()
        if runner_python != "":
            runner_path = Path(runner_python).expanduser()
            if not runner_path.is_absolute():
                runner_path = self.project_root.joinpath(runner_path)
            self.runner_python = str(runner_path)
        else:
            self.runner_python = sys.executable
        self.title("Scheduler Control Panel")
        self.geometry("1600x980")
        self.minsize(1280, 760)

        self.status_var = tk.StringVar(value="Ready")
        self.current_cmd_var = tk.StringVar(value="")

        self.process: subprocess.Popen | None = None
        self.output_thread: threading.Thread | None = None
        self.output_queue_max_items = max(200, int(os.getenv("SCHEDULER_GUI_QUEUE_MAX_ITEMS", "5000")))
        self.output_queue: queue.Queue = queue.Queue(maxsize=self.output_queue_max_items)
        self._dropped_output_lines = 0
        self._queue_drop_notice_pending = False

        # Safety limits to prevent the launched scripts from taking down the host.
        self.max_process_runtime_s = max(60, int(os.getenv("SCHEDULER_GUI_MAX_RUNTIME_S", "21600")))
        self.max_process_rss_mb = max(512, int(os.getenv("SCHEDULER_GUI_MAX_RSS_MB", "12288")))
        self.child_rlimit_as_mb = max(512, int(os.getenv("SCHEDULER_GUI_RLIMIT_AS_MB", "16384")))
        self.max_output_chars = max(100000, int(os.getenv("SCHEDULER_GUI_MAX_OUTPUT_CHARS", "1200000")))
        self._command_started_at: float | None = None
        self._watchdog_after_id: str | None = None

        self._build_layout()
        self._show_page("Generator")

    def _build_layout(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        main = ttk.Panedwindow(self, orient="horizontal")
        main.grid(row=0, column=0, sticky="nsew")

        nav = ttk.Frame(main, width=220)
        nav.pack_propagate(False)
        main.add(nav, weight=0)

        right = ttk.Panedwindow(main, orient="vertical")
        main.add(right, weight=1)

        page_container = ttk.Frame(right)
        page_container.columnconfigure(0, weight=1)
        page_container.rowconfigure(0, weight=1)
        right.add(page_container, weight=4)

        terminal_frame = ttk.LabelFrame(right, text="CLI Output")
        terminal_frame.columnconfigure(0, weight=1)
        terminal_frame.rowconfigure(1, weight=1)
        right.add(terminal_frame, weight=1)

        ttk.Label(nav, text="Scheduler", font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=12, pady=(12, 8))
        ttk.Separator(nav, orient="horizontal").pack(fill="x", padx=10, pady=(0, 10))

        self.page_buttons: dict[str, ttk.Button] = {}
        for page_name in ["Generator", "Solving", "Analysis / Plot"]:
            btn = ttk.Button(nav, text=page_name, command=lambda n=page_name: self._show_page(n))
            btn.pack(fill="x", padx=10, pady=4)
            self.page_buttons[page_name] = btn

        ttk.Separator(nav, orient="horizontal").pack(fill="x", padx=10, pady=12)
        ttk.Button(nav, text="Stop running command", command=self.stop_command).pack(fill="x", padx=10)

        self.pages: dict[str, ttk.Frame] = {
            "Generator": GeneratorPage(page_container, self),
            "Solving": SolvingPage(page_container, self),
            "Analysis / Plot": AnalysisPlotPage(page_container, self),
        }
        for frame in self.pages.values():
            frame.grid(row=0, column=0, sticky="nsew")

        terminal_actions = ttk.Frame(terminal_frame)
        terminal_actions.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        terminal_actions.columnconfigure(0, weight=1)
        ttk.Button(terminal_actions, text="Clear", command=self.clear_output).pack(side="left")
        ttk.Label(terminal_actions, textvariable=self.current_cmd_var, foreground="#666").pack(side="left", padx=(10, 0))

        self.output_text = ScrolledText(terminal_frame, wrap="none")
        self.output_text.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.output_text.bind("<Control-c>", self._on_output_copy_shortcut)
        self.output_text.bind("<Control-C>", self._on_output_copy_shortcut)
        self.output_text.bind("<Control-Insert>", self._on_output_copy_shortcut)
        self.output_text.bind("<Button-3>", self._show_output_context_menu)

        self.output_context_menu = tk.Menu(self, tearoff=0)
        self.output_context_menu.add_command(label="Copy", command=self.copy_output_selection)
        self.output_context_menu.add_command(label="Select all", command=self.select_all_output)

        status_bar = ttk.Frame(self)
        status_bar.grid(row=1, column=0, sticky="ew")
        status_bar.columnconfigure(0, weight=1)
        ttk.Separator(status_bar, orient="horizontal").grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(status_bar, textvariable=self.status_var).grid(row=1, column=0, sticky="w", padx=8, pady=4)

    def _show_page(self, page_name: str):
        frame = self.pages[page_name]
        frame.tkraise()
        self.set_status(f"Viewing {page_name}")

    def set_status(self, message: str):
        self.status_var.set(message)

    def append_output(self, text: str):
        self.output_text.insert(tk.END, text)
        try:
            total_chars = int(self.output_text.count("1.0", "end-1c", "chars")[0])
        except Exception:
            total_chars = 0
        if total_chars > self.max_output_chars:
            trim_chars = total_chars - self.max_output_chars
            self.output_text.delete("1.0", f"1.0+{trim_chars}c")
        self.output_text.see(tk.END)

    def clear_output(self):
        self.output_text.delete("1.0", tk.END)

    def copy_output_selection(self):
        try:
            selected_text = self.output_text.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            self.set_status("No output text selected.")
            return

        self.clipboard_clear()
        self.clipboard_append(selected_text)
        self.update_idletasks()
        self.set_status("Copied output selection.")

    def select_all_output(self):
        self.output_text.focus_set()
        self.output_text.tag_add(tk.SEL, "1.0", "end-1c")
        self.output_text.mark_set(tk.INSERT, "1.0")
        self.output_text.see(tk.INSERT)
        return "break"

    def _on_output_copy_shortcut(self, _event=None):
        self.copy_output_selection()
        return "break"

    def _show_output_context_menu(self, event):
        self.output_text.focus_set()
        try:
            self.output_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.output_context_menu.grab_release()
        return "break"

    def resolve_path(self, path_str: str) -> Path:
        path = Path(path_str).expanduser()
        if not path.is_absolute():
            path = self.project_root.joinpath(path)
        return path

    def browse_file(self, target_var: tk.StringVar, filetypes):
        initial = self.resolve_path(target_var.get() or ".")
        selected = filedialog.askopenfilename(
            title="Select file",
            initialdir=str(initial.parent if initial.exists() else self.project_root),
            filetypes=filetypes)
        if selected:
            selected_path = Path(selected)
            try:
                target_var.set(str(selected_path.relative_to(self.project_root)))
            except ValueError:
                target_var.set(str(selected_path))

    def browse_directory(self, target_var: tk.StringVar):
        initial = self.resolve_path(target_var.get() or ".")
        selected = filedialog.askdirectory(
            title="Select directory",
            initialdir=str(initial if initial.exists() else self.project_root))
        if selected:
            selected_path = Path(selected)
            try:
                target_var.set(str(selected_path.relative_to(self.project_root)))
            except ValueError:
                target_var.set(str(selected_path))

    def open_path(self, path: Path):
        if not path.exists():
            messagebox.showerror("Path not found", f"Cannot open:\n{path}")
            return

        try:
            if sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Open error", f"Failed to open path:\n{exc}")

    def _format_command(self, cmd: list[str]) -> str:
        return " ".join(shlex.quote(token) for token in cmd)

    def _read_vm_rss_kb(self, pid: int) -> int:
        try:
            with open(f"/proc/{pid}/status", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(parts[1])
        except Exception:
            return 0
        return 0

    def _read_children_pids(self, pid: int) -> list[int]:
        try:
            with open(f"/proc/{pid}/task/{pid}/children", "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
        except Exception:
            return []
        if raw == "":
            return []
        out: list[int] = []
        for token in raw.split():
            try:
                out.append(int(token))
            except ValueError:
                continue
        return out

    def _process_tree_rss_mb(self, root_pid: int) -> float:
        if not sys.platform.startswith("linux"):
            return 0.0

        total_kb = 0
        stack = [root_pid]
        seen: set[int] = set()
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            total_kb += self._read_vm_rss_kb(pid)
            stack.extend(self._read_children_pids(pid))
        return total_kb / 1024.0

    def _kill_running_process(self, reason: str):
        if self.process is None or self.process.poll() is not None:
            return

        pid = self.process.pid
        self.append_output(f"\n[SAFETY STOP] {reason}\n")
        self.set_status("Safety stop triggered.")

        try:
            if sys.platform.startswith("linux") or sys.platform == "darwin":
                os.killpg(pid, signal.SIGKILL)
            else:
                self.process.kill()
        except Exception as exc:
            self.append_output(f"\n[SAFETY STOP ERROR] {exc}\n")
            try:
                self.process.kill()
            except Exception:
                pass

    def _poll_watchdog(self):
        self._watchdog_after_id = None
        if self.process is None or self.process.poll() is not None:
            return

        reason: str | None = None

        if self._command_started_at is not None:
            elapsed = time.time() - self._command_started_at
            if elapsed > self.max_process_runtime_s:
                reason = f"Runtime limit exceeded ({elapsed:.0f}s > {self.max_process_runtime_s}s)."

        if reason is None and self.max_process_rss_mb > 0:
            rss_mb = self._process_tree_rss_mb(self.process.pid)
            if rss_mb > self.max_process_rss_mb:
                reason = f"Memory limit exceeded ({rss_mb:.0f}MB > {self.max_process_rss_mb}MB)."

        if reason is not None:
            self._kill_running_process(reason)
            return

        self._watchdog_after_id = self.after(500, self._poll_watchdog)

    def _build_popen_kwargs(self, cmd: list[str]) -> dict:
        kwargs: dict = {
            "args": cmd,
            "cwd": str(self.project_root),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "bufsize": 1}

        if sys.platform.startswith("linux") or sys.platform == "darwin":
            kwargs["start_new_session"] = True

            def _preexec():
                if self.child_rlimit_as_mb > 0:
                    try:
                        import resource  # POSIX only
                        limit = self.child_rlimit_as_mb * 1024 * 1024
                        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
                    except Exception:
                        pass

            kwargs["preexec_fn"] = _preexec

        return kwargs

    def start_command(self, cmd: list[str]):
        if self.process is not None and self.process.poll() is None:
            messagebox.showwarning(
                "Command running",
                "Another command is still running. Stop it or wait for completion.")
            return

        self.output_queue = queue.Queue(maxsize=self.output_queue_max_items)
        self._dropped_output_lines = 0
        self._queue_drop_notice_pending = False
        self._command_started_at = time.time()

        if self._watchdog_after_id is not None:
            self.after_cancel(self._watchdog_after_id)
            self._watchdog_after_id = None

        try:
            self.process = subprocess.Popen(**self._build_popen_kwargs(cmd))
        except Exception as exc:
            messagebox.showerror("Launch error", f"Failed to start command:\n{exc}")
            self.process = None
            self._command_started_at = None
            return

        cmd_display = self._format_command(cmd)
        self.current_cmd_var.set(cmd_display)
        self.set_status("Running command...")
        self.append_output(f"\n$ {cmd_display}\n")
        self.append_output(
            f"[SAFETY] runtime<={self.max_process_runtime_s}s, rss<={self.max_process_rss_mb}MB, "
            f"address-space<={self.child_rlimit_as_mb}MB\n")

        self.output_thread = threading.Thread(target=self._read_output, daemon=True)
        self.output_thread.start()
        self._watchdog_after_id = self.after(500, self._poll_watchdog)
        self.after(80, self._poll_output)

    def _read_output(self):
        assert self.process is not None
        proc = self.process

        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    try:
                        self.output_queue.put_nowait(("out", line))
                    except queue.Full:
                        self._dropped_output_lines += 1
                        self._queue_drop_notice_pending = True
            return_code = proc.wait()
            try:
                self.output_queue.put_nowait(("done", return_code))
            except queue.Full:
                pass
        except Exception as exc:
            try:
                self.output_queue.put_nowait(("out", f"\n[ERROR] {exc}\n"))
            except queue.Full:
                pass
            try:
                self.output_queue.put_nowait(("done", -1))
            except queue.Full:
                pass

    def _poll_output(self):
        done = False

        if self._queue_drop_notice_pending and self._dropped_output_lines > 0:
            dropped = self._dropped_output_lines
            self._dropped_output_lines = 0
            self._queue_drop_notice_pending = False
            self.append_output(f"\n[WARN] Dropped {dropped} output lines (queue overflow).\n")

        while True:
            try:
                kind, payload = self.output_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "out":
                self.append_output(payload)
            elif kind == "done":
                done = True
                code = int(payload)
                self.append_output(f"\n[PROCESS EXIT CODE {code}]\n")
                if code == 0:
                    self.set_status("Completed successfully.")
                else:
                    self.set_status(f"Completed with errors (exit code {code}).")

        if done:
            if self._watchdog_after_id is not None:
                self.after_cancel(self._watchdog_after_id)
                self._watchdog_after_id = None
            self._command_started_at = None
            self.process = None
            return

        if self.process is not None and self.process.poll() is None:
            self.after(80, self._poll_output)
        else:
            if self._watchdog_after_id is not None:
                self.after_cancel(self._watchdog_after_id)
                self._watchdog_after_id = None
            self._command_started_at = None
            self.process = None
            self.set_status("Ready")

    def stop_command(self):
        if self.process is None or self.process.poll() is not None:
            self.set_status("No running command.")
            return

        self.append_output("\n[STOP REQUESTED] Force-killing process group.\n")
        self._kill_running_process("Manual stop requested.")


if __name__ == "__main__":
    def _maybe_switch_gui_renderer():
        # On Linux/Wayland Conda Tk can look blurry; fallback to distro Python
        # while keeping Conda as runner for launched scripts.
        if not sys.platform.startswith("linux"):
            return
        if os.getenv("SCHEDULER_GUI_DISABLE_AUTO_RENDERER_SWITCH", "").strip().lower() in {"1", "true", "yes"}:
            return
        if os.getenv("SCHEDULER_GUI_RENDERER_SWITCH_DONE", "").strip() == "1":
            return
        if os.getenv("XDG_SESSION_TYPE", "").strip().lower() != "wayland":
            return

        try:
            current_python = Path(sys.executable).resolve()
        except Exception:
            return
        system_python = Path("/usr/bin/python3")
        if not system_python.exists():
            return
        if current_python == system_python.resolve():
            return

        current_str = str(current_python)
        if "miniconda3" not in current_str and "anaconda3" not in current_str:
            return

        env = os.environ.copy()
        env["SCHEDULER_GUI_RENDERER_SWITCH_DONE"] = "1"
        env.setdefault("SCHEDULER_GUI_RUNNER_PYTHON", current_str)
        argv = [str(system_python), *sys.argv]
        os.execvpe(str(system_python), argv, env)

    _maybe_switch_gui_renderer()
    app = ControlPanelApp()
    app.mainloop()
