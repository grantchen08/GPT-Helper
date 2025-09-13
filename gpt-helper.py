import sys
import os
import difflib
from pathlib import Path
from PySide6 import QtWidgets, QtCore, QtGui
from chunked_editor import ChunkedPlainTextEdit

# You must run: pip install thefuzz python-Levenshtein
from thefuzz import fuzz
import os

# App identity for QSettings
QtCore.QCoreApplication.setOrganizationName("Grant")
QtCore.QCoreApplication.setOrganizationDomain("grantech.co")
QtCore.QCoreApplication.setApplicationName("InteractivePatchHelper")


class LineNumberArea(QtWidgets.QWidget):
    """A widget that draws line numbers for a QPlainTextEdit."""
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QtCore.QSize(self.editor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        self.editor.lineNumberAreaPaintEvent(event)


class CodeEditor(QtWidgets.QPlainTextEdit):
    """A QPlainTextEdit with a line number area and support for external highlights."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lineNumberArea = LineNumberArea(self)

        # Holds external selections (e.g., highlight for matched/applied region)
        self._externalSelections: list[QtWidgets.QTextEdit.ExtraSelection] = []

        self.blockCountChanged.connect(self.updateLineNumberAreaWidth)
        self.updateRequest.connect(self.updateLineNumberArea)
        self.cursorPositionChanged.connect(self.highlightCurrentLine)

        self.updateLineNumberAreaWidth(0)
        self.highlightCurrentLine()

    def setExternalSelections(self, selections: list[QtWidgets.QTextEdit.ExtraSelection]):
        """Set extra selections to be rendered alongside current-line highlight."""
        self._externalSelections = selections or []
        # Re-apply highlight to merge them
        self.highlightCurrentLine()

    def clearExternalSelections(self):
        self.setExternalSelections([])

    def lineNumberAreaWidth(self):
        digits = 1
        count = max(1, self.blockCount())
        while count >= 10:
            count //= 10
            digits += 1
        space = 10 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def updateLineNumberAreaWidth(self, _):
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def updateLineNumberArea(self, rect, dy):
        if dy:
            self.lineNumberArea.scroll(0, dy)
        else:
            self.lineNumberArea.update(0, rect.y(), self.lineNumberArea.width(), rect.height())

        if rect.contains(self.viewport().rect()):
            self.updateLineNumberAreaWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.lineNumberArea.setGeometry(QtCore.QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))

    def highlightCurrentLine(self):
        extraSelections = []
        if not self.isReadOnly():
            selection = QtWidgets.QTextEdit.ExtraSelection()
            lineColor = QtGui.QColor(QtCore.Qt.yellow).lighter(160)
            selection.format.setBackground(lineColor)
            selection.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extraSelections.append(selection)

        # Merge with external selections (e.g., matched/applied region)
        if self._externalSelections:
            extraSelections.extend(self._externalSelections)

        self.setExtraSelections(extraSelections)

    def lineNumberAreaPaintEvent(self, event):
        painter = QtGui.QPainter(self.lineNumberArea)
        painter.fillRect(event.rect(), QtCore.Qt.lightGray)

        block = self.firstVisibleBlock()
        blockNumber = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(blockNumber + 1)
                painter.setPen(QtCore.Qt.black)
                painter.drawText(
                    0, int(top), self.lineNumberArea.width() - 5, self.fontMetrics().height(),
                    QtCore.Qt.AlignRight, number
                )

            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            blockNumber += 1


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Interactive Patch Helper")
        self.resize(1400, 900)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        self.setCentralWidget(central)

        # Top controls
        top_row = QtWidgets.QHBoxLayout()
        layout.addLayout(top_row)
        self.root_edit = QtWidgets.QLineEdit()
        self.root_edit.setReadOnly(True)
        choose_btn = QtWidgets.QPushButton("Choose Root…")
        choose_btn.clicked.connect(self.choose_root)
        self.debug_check = QtWidgets.QCheckBox("Debug logs")
        # Use toggled(bool) to avoid int/enum comparison issues
        self.debug_check.toggled.connect(self._on_debug_toggled)
        self.relaunch_btn = QtWidgets.QPushButton("Relaunch")
        self.relaunch_btn.clicked.connect(self.relaunch_app)
        # Apply button for hovered chunk (enabled/disabled dynamically)
        self.apply_btn = QtWidgets.QPushButton("Apply Hovered Chunk")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply_hovered_chunk_if_possible)

        top_row.addWidget(QtWidgets.QLabel("Root:"))
        top_row.addWidget(self.root_edit, stretch=1)
        top_row.addWidget(choose_btn)
        top_row.addWidget(self.debug_check)
        top_row.addWidget(self.relaunch_btn)
        top_row.addWidget(self.apply_btn)

        # Main editor area with a splitter
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter, stretch=1)

        # Left: Patch editor
        self.patch_edit = ChunkedPlainTextEdit(context_before=3, debug=False)
        self.patch_edit.setPlaceholderText("Paste patch text here…")
        # For stable geometry
        self.patch_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.patch_edit.chunkHovered.connect(self._on_chunk_hovered)
        # Context menu "Apply Chunk" handler
        self.patch_edit.chunkApplyRequested.connect(self._on_chunk_apply_requested)

        # Right: File viewer (in-memory single source of truth)
        self.file_viewer = CodeEditor()
        self.file_viewer.setReadOnly(False)
        self.file_viewer.setPlaceholderText("Hover a chunk on the left; the file will load here if the view is empty.")
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.file_viewer.setFont(fixed_font)
        # For stable geometry
        self.file_viewer.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        splitter.addWidget(self.patch_edit)
        splitter.addWidget(self.file_viewer)
        splitter.setSizes([700, 700])

        # Bottom dock: Root directory tree view (replaces diff preview)
        self.fs_model = QtWidgets.QFileSystemModel(self)
        self.fs_model.setRootPath(os.getcwd())
        # Optional: filter to show files/dirs; adjust as needed
        self.fs_model.setFilter(QtCore.QDir.AllEntries | QtCore.QDir.NoDotAndDotDot | QtCore.QDir.AllDirs)

        self.dir_tree = QtWidgets.QTreeView(self)
        self.dir_tree.setModel(self.fs_model)
        self.dir_tree.setRootIndex(self.fs_model.index(os.getcwd()))
        self.dir_tree.setSortingEnabled(True)
        self.dir_tree.sortByColumn(0, QtCore.Qt.AscendingOrder)
        self.dir_tree.setAlternatingRowColors(True)
        self.dir_tree.setAnimated(True)
        self.dir_tree.setHeaderHidden(False)

        self.diff_dock = QtWidgets.QDockWidget("Root Browser", self)
        self.diff_dock.setAllowedAreas(QtCore.Qt.BottomDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        self.diff_dock.setWidget(self.dir_tree)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.diff_dock)

        self.statusBar().showMessage("Ready")

        # Debug flag
        self._debug = False

        # Track current hovered chunk info
        self._hover_chunk_idx: int | None = None
        self._hover_chunk_file: str | None = None
        self._hover_context_lines: list[str] = []
        self._hover_applicable: bool = False
        self._hover_already_applied: bool = False
        self._hover_apply_start_idx: int | None = None
        self._hover_highlight_len: int = 0

        # When user edits the right buffer, clear stale highlights and re-evaluate current hover state (debounced)
        self._debounce_timer = QtCore.QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(150)
        self._debounce_timer.timeout.connect(self._reevaluate_hover_state_once)
        self.file_viewer.textChanged.connect(self._on_file_text_changed)

        self.load_settings()

    def current_view_file(self) -> str | None:
        v = self.file_viewer.property("current_file")
        return str(v) if v else None

    def is_view_empty(self) -> bool:
        return (self.file_viewer.toPlainText() == "") and (self.current_view_file() is None)

    @QtCore.Slot(bool)
    def _on_debug_toggled(self, on: bool):
        self._apply_debug_state(on)

    def _apply_debug_state(self, on: bool):
        self._debug = on
        self.patch_edit.set_debug(on)  # pass to child widget for its own logs
        self.statusBar().showMessage("Debug logging " + ("enabled" if on else "disabled"), 2000)
        print("\n--- Debug logging " + ("enabled" if on else "disabled") + " ---")

    @QtCore.Slot()
    def _on_file_text_changed(self):
        # Clear transient highlight and re-evaluate applicability after a short debounce
        self.file_viewer.clearExternalSelections()
        self._debounce_timer.stop()
        self._debounce_timer.start()

    @QtCore.Slot()
    def _reevaluate_hover_state_once(self):
        if self._hover_chunk_idx is None or self._hover_chunk_idx < 0:
            self.apply_btn.setEnabled(False)
            self._clear_diff_preview()
            return
        # Re-run applicability based on current buffer
        self._evaluate_and_update_ui_for_hovered_chunk()

    @QtCore.Slot(int, str, list, QtGui.QTextBlock)
    def _on_chunk_hovered(self, chunk_idx: int, file_path: str, context_lines: list, _first_context_block: QtGui.QTextBlock):
        """
        Loads file only if the right panel is empty; otherwise reuses current buffer.
        Computes applicability of hovered chunk against the current buffer and updates apply button and highlight.
        """
        # Clear when leaving a chunk
        if chunk_idx == -1 or not file_path:
            self._hover_chunk_idx = None
            self._hover_chunk_file = None
            self._hover_context_lines = []
            self._hover_applicable = False
            self._hover_already_applied = False
            self._hover_apply_start_idx = None
            self._hover_highlight_len = 0
            self.file_viewer.clearExternalSelections()
            self.apply_btn.setEnabled(False)
            self._clear_diff_preview()
            return

        # Update hover context
        self._hover_chunk_idx = chunk_idx
        self._hover_chunk_file = file_path.replace("\\", "/")
        self._hover_context_lines = list(context_lines)

        if self._debug:
            print("\n" + "=" * 20 + f" HOVER CHUNK #{chunk_idx + 1} " + "=" * 20)
            print(f"File Path (rel): {self._hover_chunk_file}")
            print("Context lines to search for:")
            for line in context_lines:
                print(f"  > {line}")
            print("-" * 58)

        # Resolve full path (for loading if needed)
        root_dir = self.root_edit.text().strip()
        if not root_dir:
            self.statusBar().showMessage("Root directory not set.", 3000)
            self.apply_btn.setEnabled(False)
            self._clear_diff_preview()
            return

        root = Path(root_dir).expanduser()
        rel = Path(self._hover_chunk_file)
        full_path = (root / rel).resolve(strict=False)

        # Load only if right panel is empty
        current_path = self.current_view_file()
        if self.is_view_empty():
            # Try to load file if it exists; otherwise, keep an empty buffer but set current_file property
            if full_path.is_file():
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    self.file_viewer.setPlainText(content)
                    self.file_viewer.setProperty("current_file", str(full_path))
                    self.statusBar().showMessage(f"Loaded: {rel.as_posix()}", 4000)
                except Exception as e:
                    self.file_viewer.setPlainText(f"Error reading file: {full_path}\n\n{str(e)}")
                    self.file_viewer.setProperty("current_file", str(full_path))
                    self.statusBar().showMessage(f"Error reading {rel.as_posix()}", 4000)
            else:
                # Start with empty buffer but remember target file path
                self.file_viewer.setPlainText("")
                self.file_viewer.setProperty("current_file", str(full_path))
                self.statusBar().showMessage(f"File not found; editing new buffer: {rel.as_posix()}", 4000)
        else:
            # Reuse existing buffer; if it's a different file, do not switch
            if current_path and Path(current_path) != full_path:
                self.statusBar().showMessage(
                    f"Right panel has {Path(current_path).name}; chunk is for {rel.as_posix()}. Not switching.", 5000
                )
                self.apply_btn.setEnabled(False)
                self.file_viewer.clearExternalSelections()
                self._clear_diff_preview()
                return

        # Evaluate applicability and update highlight/UI
        self._evaluate_and_update_ui_for_hovered_chunk()

    def _evaluate_and_update_ui_for_hovered_chunk(self):
        """Evaluate applicability of the currently hovered chunk against the current buffer. Update UI accordingly."""
        if self._hover_chunk_idx is None or not self._hover_chunk_file:
            self.apply_btn.setEnabled(False)
            self._clear_diff_preview()
            return

        # Extract details for hovered chunk
        details = self.patch_edit.get_chunk_details(self._hover_chunk_idx)
        if not details:
            self.apply_btn.setEnabled(False)
            self._clear_diff_preview()
            return

        # Ensure file path matches the loaded buffer (we already constrained on hover, but double-check)
        current_path = self.current_view_file()
        if not current_path or (details["file_path"].replace("\\", "/") not in str(current_path).replace("\\", "/")):
            self.apply_btn.setEnabled(False)
            self._clear_diff_preview()
            return

        # Compute match and applicability on current buffer
        lines = self.file_viewer.toPlainText().splitlines()
        match_line_num = self._find_best_match(lines, details["context_lines"], min_score=60)
        applicable, already_applied, apply_start_idx, highlight_len = self._evaluate_chunk_applicability(
            lines, details, match_line_num
        )

        self._hover_applicable = applicable
        self._hover_already_applied = already_applied
        self._hover_apply_start_idx = apply_start_idx
        self._hover_highlight_len = highlight_len

        # Prefer highlighting the matched context if available and non-empty
        n_ctx = details.get("n_context", 0)
        if match_line_num is not None and n_ctx > 0:
            self._scroll_target_line_to_top(self.file_viewer, match_line_num)
            # Highlight context (blue) or applied region will be highlighted when applied
            self._highlight_context_in_file_viewer(match_line_num, n_ctx)
        # If there are no context lines in this chunk, fall back to previewing the edit region
        elif self._hover_apply_start_idx is not None and self._hover_highlight_len > 0:
            applied_start_line = self._hover_apply_start_idx + 1  # 1-based
            self._scroll_target_line_to_top(self.file_viewer, applied_start_line)
            self._highlight_context_in_file_viewer(applied_start_line, self._hover_highlight_len)
        else:
            # Try a simple exact search on the first non-empty context line as a fallback
            first_ctx_line = next((l for l in details["context_lines"] if l.strip()), None)
            if first_ctx_line:
                try:
                    pos = lines.index(first_ctx_line)
                    self._scroll_target_line_to_top(self.file_viewer, pos + 1)
                    self._highlight_context_in_file_viewer(pos + 1, 1)
                except ValueError:
                    self.file_viewer.clearExternalSelections()
            else:
                self.file_viewer.clearExternalSelections()

        # Update Apply button state and diff preview
        if already_applied:
            self.apply_btn.setEnabled(False)
            self.apply_btn.setToolTip("Chunk already applied to current buffer.")
            self._show_diff_preview_already_applied(details)
        elif applicable and apply_start_idx is not None:
            self.apply_btn.setEnabled(True)
            self.apply_btn.setToolTip("Apply this chunk to the current buffer.")
            self._update_diff_preview(details, apply_start_idx)
        else:
            self.apply_btn.setEnabled(False)
            self.apply_btn.setToolTip("Context not found or ambiguous in current buffer.")
            self._clear_diff_preview(show_message="Context not found or ambiguous.")

    @QtCore.Slot(int)
    def _on_chunk_apply_requested(self, chunk_idx: int):
        """Apply from the left context menu; internally delegates to the same logic as the top button."""
        # If user clicked a different chunk, simulate hover for it first
        if self._hover_chunk_idx != chunk_idx:
            details = self.patch_edit.get_chunk_details(chunk_idx)
            if not details:
                QtWidgets.QMessageBox.warning(self, "Apply Chunk", "Invalid chunk.")
                return
            if not self._hover_chunk_file:
                self._hover_chunk_file = details["file_path"].replace("\\", "/")
            self._hover_chunk_idx = chunk_idx
            self._hover_context_lines = details["context_lines"]
            self._evaluate_and_update_ui_for_hovered_chunk()

        self._apply_hovered_chunk_if_possible()

    def _apply_hovered_chunk_if_possible(self):
        """Apply the currently hovered chunk to the in-memory buffer, if applicable."""
        if self._hover_chunk_idx is None:
            return

        details = self.patch_edit.get_chunk_details(self._hover_chunk_idx)
        if not details:
            QtWidgets.QMessageBox.warning(self, "Apply Chunk", "Invalid chunk.")
            return

        current_path = self.current_view_file()
        rel = details["file_path"]
        if not current_path or (details["file_path"].replace("\\", "/") not in str(current_path).replace("\\", "/")):
            QtWidgets.QMessageBox.information(self, "Apply Chunk", f"Open the target file first: {rel}")
            return

        # Ensure applicability computed
        lines = self.file_viewer.toPlainText().splitlines()
        if self._hover_apply_start_idx is None or not self._hover_applicable:
            match_line_num = self._find_best_match(lines, details["context_lines"], min_score=60)
            applicable, already, start_idx, hlen = self._evaluate_chunk_applicability(lines, details, match_line_num)
            if not applicable or already:
                reason = "already applied" if already else "context not found"
                QtWidgets.QMessageBox.information(self, "Apply Chunk", f"Cannot apply: {reason}.")
                return
            self._hover_apply_start_idx = start_idx
            self._hover_highlight_len = hlen

        # Apply to in-memory buffer only
        new_lines = self._apply_at(lines, self._hover_apply_start_idx, details["removed_lines"], details["added_lines"])
        self.file_viewer.setPlainText("\n".join(new_lines))
        self.file_viewer.document().setModified(True)

        applied_start_line = self._hover_apply_start_idx + 1  # 1-based
        self._scroll_target_line_to_top(self.file_viewer, applied_start_line)
        self._highlight_context_in_file_viewer(
            applied_start_line,
            max(1, len(details["added_lines"]) or len(details["removed_lines"])),
            color=QtGui.QColor(170, 255, 170, 140),
        )

        self.statusBar().showMessage(f"Applied chunk (in-memory only) to: {Path(current_path).name}", 4000)

        # After applying, re-evaluate so next hover highlights correctly
        self._evaluate_and_update_ui_for_hovered_chunk()

    def _find_best_match(self, target_lines: list[str], query_lines: list[str], min_score=75) -> int | None:
        """Finds the best fuzzy match for a block of lines. Returns 1-based starting line number."""
        if not query_lines or not target_lines:
            if self._debug:
                print("[FUZZY] Canceled: No query or target lines.")
            return None

        query_str = "\n".join(query_lines)
        num_query_lines = len(query_lines)
        best_score, best_line_num = -1, -1

        for i in range(len(target_lines) - num_query_lines + 1):
            window_lines = target_lines[i: i + num_query_lines]
            window_str = "\n".join(window_lines)
            score = fuzz.ratio(query_str, window_str)
            if score > best_score:
                best_score = score
                best_line_num = i + 1  # 1-based

        if self._debug:
            print(f"[FUZZY] Best match score: {best_score}")
            if best_score >= min_score:
                print(f"[FUZZY] SUCCESS: Found match at line {best_line_num} (score >= {min_score})")
            else:
                print(f"[FUZZY] FAILED: Best score below threshold of {min_score}")

        return best_line_num if best_score >= min_score else None

    def _evaluate_chunk_applicability(self, lines: list[str], details: dict, match_line_num: int | None):
        """
        Determine if a chunk can be applied or is already applied against the given lines.
        Returns (applicable: bool, already_applied: bool, apply_start_idx: int | None, highlight_len: int).
        """
        context_lines = details["context_lines"]
        n_context = details["n_context"]
        removed = details["removed_lines"]
        added = details["added_lines"]

        if not context_lines or match_line_num is None:
            return False, False, None, 0

        base_idx = (match_line_num - 1) + n_context  # where changes should begin after the matched context block

        # Heuristic for "already applied":
        # - If added lines match at base_idx, and (if removals exist) the removal block cannot be found nearby, consider already applied.
        if added and self._slice_equals(lines, base_idx, added):
            if removed:
                nearby_removed = self._find_exact_sequence_near(lines, removed, base_idx, window=30)
                if nearby_removed is None:
                    return False, True, None, len(added)
            else:
                return False, True, None, len(added)

        # Check if we can apply:
        start_idx = base_idx
        if removed:
            if not self._slice_equals(lines, start_idx, removed):
                found = self._find_exact_sequence_near(lines, removed, base_idx, window=30)
                if found is None:
                    return False, False, None, 0
                start_idx = found

        # Applicable (replacement or insertion)
        highlight_len = max(1, len(added) if added else (len(removed) if removed else 1))
        return True, False, start_idx, highlight_len

    def _update_diff_preview(self, details: dict, start_idx: int):
        """No-op: diff preview replaced by root directory tree view."""
        return

    def _show_diff_preview_already_applied(self, details: dict):
        """No-op: diff preview replaced by root directory tree view."""
        return

    def _clear_diff_preview(self, show_message: str | None = None):
        """No-op: diff preview replaced by root directory tree view."""
        return

    def _scroll_target_line_to_top(self, target_editor: QtWidgets.QPlainTextEdit, target_line_num: int):
        """Scrolls the target editor so that target_line_num is at the top of the viewport."""
        target_block = target_editor.document().findBlockByNumber(target_line_num - 1)
        if not target_block.isValid():
            if self._debug:
                print("[ALIGN] FAILED: Target line number is invalid.")
            return

        cur = target_editor.textCursor()
        cur.setPosition(target_block.position())
        target_editor.setTextCursor(cur)

        # Ensure it's visible first, then nudge so the line sits at the top of the viewport.
        target_editor.ensureCursorVisible()

        rect = target_editor.cursorRect(cur)  # viewport coordinates
        sb = target_editor.verticalScrollBar()
        before = sb.value()
        sb.setValue(before + rect.top())

        if self._debug:
            print(f"[ALIGN] Top-align: cursor_top={rect.top()} scrollbar: {before} -> {sb.value()}")

    def _highlight_context_in_file_viewer(self, start_line_num: int, num_lines: int,
                                          color: QtGui.QColor = QtGui.QColor(128, 200, 255, 120)):
        """Highlights num_lines lines starting at 1-based start_line_num in the file_viewer."""
        doc = self.file_viewer.document()
        start_idx = max(0, start_line_num - 1)
        end_idx = start_idx + max(0, num_lines - 1)

        start_block = doc.findBlockByNumber(start_idx)
        end_block = doc.findBlockByNumber(end_idx)

        if not start_block.isValid() or not end_block.isValid():
            self.file_viewer.clearExternalSelections()
            if self._debug:
                print("[HILITE] Invalid block range for highlighting.")
            return

        cur = QtGui.QTextCursor(doc)
        cur.setPosition(start_block.position())
        # Span to end of end_block's text
        end_pos = end_block.position() + len(end_block.text())
        cur.setPosition(end_pos, QtGui.QTextCursor.KeepAnchor)

        sel = QtWidgets.QTextEdit.ExtraSelection()
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtGui.QBrush(color))
        fmt.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
        sel.format = fmt
        sel.cursor = cur

        # Apply without losing current-line highlight
        self.file_viewer.setExternalSelections([sel])

        if self._debug:
            print(f"[HILITE] Highlighting lines {start_idx + 1}..{end_idx + 1}")

    @staticmethod
    def _slice_equals(haystack: list[str], start: int, needle: list[str]) -> bool:
        if start < 0 or start + len(needle) > len(haystack):
            return False
        return haystack[start:start + len(needle)] == needle

    @staticmethod
    def _find_exact_sequence_near(lines: list[str], seq: list[str], around: int, window: int = 30) -> int | None:
        """
        Search for an exact sequence 'seq' within +/- 'window' lines around 'around'.
        Returns 0-based start index or None.
        """
        n = len(lines)
        m = len(seq)
        if m == 0:
            return around
        lo = max(0, around - window)
        hi = min(n - m, around + window)
        for i in range(lo, hi + 1):
            if lines[i:i + m] == seq:
                return i
        return None

    @staticmethod
    def _apply_at(lines: list[str], start_idx: int, removed: list[str], added: list[str]) -> list[str]:
        """Return a new list with removed block replaced by added block at start_idx."""
        if removed:
            return lines[:start_idx] + added + lines[start_idx + len(removed):]
        else:
            # pure insertion
            return lines[:start_idx] + added + lines[start_idx:]

    def choose_root(self):
        current = self.root_edit.text() or os.getcwd()
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Root Directory", current)
        if directory:
            self.root_edit.setText(directory)
            self.statusBar().showMessage(f"Root directory set to: {directory}", 3000)
            # Update tree view root to selected directory
            if hasattr(self, "fs_model") and hasattr(self, "dir_tree"):
                self.fs_model.setRootPath(directory)
                self.dir_tree.setRootIndex(self.fs_model.index(directory))

    def relaunch_app(self):
        self.save_settings()
        if getattr(sys, "frozen", False):
            program, arguments, workdir = sys.argv[0], sys.argv[1:], os.path.dirname(sys.argv[0])
        else:
            program, arguments, workdir = sys.executable, sys.argv, os.getcwd()
        ok = QtCore.QProcess.startDetached(program, arguments, workdir)
        if not ok:
            QtWidgets.QMessageBox.critical(self, "Relaunch", "Failed to start a new process.")
            return
        QtWidgets.QApplication.quit()

    def load_settings(self):
        s = QtCore.QSettings()
        geom = s.value("window/geometry")
        state = s.value("window/state")
        if geom:
            self.restoreGeometry(geom)
        if state:
            self.restoreState(state)
        root = s.value("app/rootDir", os.getcwd(), type=str)
        self.root_edit.setText(root)
        text = s.value("app/patchText", "", type=str)
        if text:
            self.patch_edit.setPlainText(text)
        debug_on = bool(s.value("app/debug", False, type=bool))

        # Initialize tree view root to saved root directory
        root_dir = self.root_edit.text()
        if hasattr(self, "fs_model") and hasattr(self, "dir_tree") and root_dir:
            self.fs_model.setRootPath(root_dir)
            self.dir_tree.setRootIndex(self.fs_model.index(root_dir))
        # Avoid triggering toggled during load; then apply explicitly
        self.debug_check.blockSignals(True)
        self.debug_check.setChecked(debug_on)
        self.debug_check.blockSignals(False)
        self._apply_debug_state(debug_on)

    def save_settings(self):
        s = QtCore.QSettings()
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/state", self.saveState())
        s.setValue("app/rootDir", self.root_edit.text())
        s.setValue("app/patchText", self.patch_edit.toPlainText())
        s.setValue("app/debug", self.debug_check.isChecked())
        s.sync()

    def closeEvent(self, event: QtGui.QCloseEvent):
        try:
            self.save_settings()
        finally:
            super().closeEvent(event)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()