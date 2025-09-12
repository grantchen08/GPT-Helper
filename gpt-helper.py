import sys
import os
from PySide6 import QtWidgets, QtCore, QtGui
from chunked_editor import ChunkedPlainTextEdit

# You must run: pip install thefuzz python-Levenshtein
from thefuzz import fuzz

# App identity for QSettings
QtCore.QCoreApplication.setOrganizationName("Grant")
QtCore.QCoreApplication.setOrganizationDomain("grantech.co")
QtCore.QCoreApplication.setApplicationName("InteractivePatchHelper")


# --- Helper classes for the line number feature ---

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

        # Holds external selections (e.g., highlight for matched context in the right pane)
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
            count /= 10
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

        # Merge with external selections (e.g., matched context highlight)
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
        self.resize(1200, 800)

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
        self.debug_check.stateChanged.connect(self._on_debug_toggled)
        self.relaunch_btn = QtWidgets.QPushButton("Relaunch")
        self.relaunch_btn.clicked.connect(self.relaunch_app)
        top_row.addWidget(QtWidgets.QLabel("Root:"))
        top_row.addWidget(self.root_edit, stretch=1)
        top_row.addWidget(choose_btn)
        top_row.addWidget(self.debug_check)
        top_row.addWidget(self.relaunch_btn)

        # Main editor area with a splitter
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter, stretch=1)

        # Left: Patch editor
        self.patch_edit = ChunkedPlainTextEdit(context_before=3, debug=False)
        self.patch_edit.setPlaceholderText("Paste patch text here…")
        # Recommended: no wrapping for more stable geometry
        self.patch_edit.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self.patch_edit.chunkHovered.connect(self._on_chunk_hovered)
        # NEW: connect Apply action from context menu (UI signal)
        self.patch_edit.chunkApplyRequested.connect(self._on_chunk_apply_requested)

        # Right: File viewer
        self.file_viewer = CodeEditor()
        self.file_viewer.setReadOnly(True)
        self.file_viewer.setPlaceholderText("Hover over a chunk on the left to see the corresponding file here.")
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.file_viewer.setFont(fixed_font)
        # Recommended: no wrapping for stable geometry
        self.file_viewer.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        splitter.addWidget(self.patch_edit)
        splitter.addWidget(self.file_viewer)
        splitter.setSizes([600, 600])

        self.statusBar().showMessage("Ready")

        # Debug flag
        self._debug = True  # or self.debug_check.isChecked()

        self.load_settings()

    @QtCore.Slot(int, str, list, QtGui.QTextBlock)
    def _on_chunk_hovered(self, chunk_idx: int, file_path: str, context_lines: list, first_context_block: QtGui.QTextBlock):
        """Loads file, fuzzy finds context, aligns the view, and highlights the matched context."""
        # Clear when leaving a chunk
        if chunk_idx == -1 or not file_path:
            self.file_viewer.clearExternalSelections()
            return

        if self._debug:
            print("\n" + "=" * 20 + f" HOVER CHUNK #{chunk_idx + 1} " + "=" * 20)
            print(f"File Path: {file_path}")
            print("Context lines to search for:")
            for line in context_lines:
                print(f"  > {line}")
            print("-" * 58)

        root_dir = self.root_edit.text()
        if not root_dir:
            self.file_viewer.setPlainText("ERROR: Root directory is not set.")
            self.file_viewer.clearExternalSelections()
            return

        full_path = os.path.join(root_dir, file_path)
        current_path = self.file_viewer.property("current_file")
        if current_path != full_path:
            try:
                if os.path.isfile(full_path):
                    with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    self.file_viewer.setPlainText(content)
                    self.file_viewer.setProperty("current_file", full_path)
                    self.statusBar().showMessage(f"Showing: {file_path}", 4000)
                else:
                    self.file_viewer.setPlainText(f"File not found: {full_path}")
                    self.file_viewer.setProperty("current_file", None)
                    self.file_viewer.clearExternalSelections()
                    self.statusBar().showMessage(f"File not found: {file_path}", 4000)
                    return
            except Exception as e:
                self.file_viewer.setPlainText(f"Error reading file: {full_path}\n\n{str(e)}")
                self.file_viewer.setProperty("current_file", None)
                self.file_viewer.clearExternalSelections()
                self.statusBar().showMessage(f"Error reading {file_path}", 4000)
                return

        # With content loaded, try to find and align/highlight the matching context
        if context_lines and first_context_block and first_context_block.isValid():
            target_lines = self.file_viewer.toPlainText().splitlines()
            match_line_num = self._find_best_match(target_lines, context_lines)

            if match_line_num is not None:
                # Align: top-align the first matched line (simple and robust)
                self._scroll_target_line_to_top(self.file_viewer, match_line_num)

                # Highlight exactly the matched context block
                self._highlight_context_in_file_viewer(match_line_num, len(context_lines))
            else:
                # No match => clear highlight
                self.file_viewer.clearExternalSelections()

    def _find_best_match(self, target_lines: list, query_lines: list, min_score=75) -> int | None:
        """Finds the best fuzzy match for a block of lines."""
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
                print(f"[FUZZY] FAILED: Best score is below threshold of {min_score}")

        return best_line_num if best_score >= min_score else None

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

    @QtCore.Slot(int)
    def _on_chunk_apply_requested(self, chunk_idx: int):
        # UI-only example: in real use you’d implement actual apply logic here
        if self._debug:
            print(f"[APPLY] User chose to apply chunk #{chunk_idx + 1} (index {chunk_idx})")

    def choose_root(self):
        current = self.root_edit.text() or os.getcwd()
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Root Directory", current)
        if directory:
            self.root_edit.setText(directory)
            self.statusBar().showMessage(f"Root directory set to: {directory}", 3000)

    def _on_debug_toggled(self, state: int):
        on = (state == QtCore.Qt.Checked)
        self._debug = on
        self.patch_edit.set_debug(on)  # pass to child widget for its own logs
        self.statusBar().showMessage("Debug logging " + ("enabled" if on else "disabled"), 2000)
        if on:
            print("\n--- Debug logging enabled ---")
        else:
            print("\n--- Debug logging disabled ---")

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
        debug_on = s.value("app/debug", False, type=bool)
        self.debug_check.setChecked(debug_on)

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
