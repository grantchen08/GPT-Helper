import sys
import os
from PySide6 import QtWidgets, QtCore, QtGui


class ChunkedPlainTextEdit(QtWidgets.QPlainTextEdit):
    """
    A QPlainTextEdit that:
    - Parses the document into "chunks" (diff/file sections) on text changes.
    - Shows a tooltip with the chunk number when hovering over a line belonging to a chunk.
    """
    chunks_recomputed = QtCore.Signal(int)  # emits total chunk count

    def __init__(self, parent=None):
        super().__init__(parent)
        # Use a monospace font for diff readability
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.setFont(fixed_font)

        self.setMouseTracking(True)
        self.document().contentsChanged.connect(self._recompute_chunks)

        self._chunk_count = 0
        self._last_hover_chunk = None  # Track last chunk tooltip to avoid flicker

        # Initial compute (for empty doc it sets everything to -1 states)
        self._recompute_chunks()

    @staticmethod
    def _is_chunk_start(line: str, next_line: str | None) -> bool:
        """Heuristics for beginning of a diff/file chunk."""
        ls = line.lstrip()
        if ls.startswith("diff --git "):
            return True
        if ls.startswith("Index: "):
            return True
        if ls.startswith("*** "):  # e.g., context diff header
            return True
        if ls.startswith("--- ") and next_line is not None and next_line.lstrip().startswith("+++ "):
            return True
        return False

    def _recompute_chunks(self):
        """
        Assign a chunk index to each text block via block.setUserState().
        -1 means "no chunk".
        0-based chunk indexing internally; tooltips show 1-based.
        """
        doc = self.document()
        block = doc.firstBlock()

        chunk_idx = -1
        prev_was_empty = True

        while block.isValid():
            text = block.text()
            # Look ahead one block for the ---/+++ pattern
            next_block = block.next()
            next_text = next_block.text() if next_block.isValid() else None

            # Decide if this block starts a new chunk
            is_start = self._is_chunk_start(text, next_text)

            # Fallback: split on blank-line-separated groups if no markers yet
            if not is_start and chunk_idx >= 0 and prev_was_empty and text.strip():
                is_start = True

            # If we haven't started any chunk and this line has content, start chunk 0
            if chunk_idx == -1 and text.strip():
                is_start = True

            if is_start:
                chunk_idx += 1

            # Tag the block with its chunk index or -1 if still before any content
            if chunk_idx >= 0:
                block.setUserState(chunk_idx)
            else:
                block.setUserState(-1)

            prev_was_empty = (len(text.strip()) == 0)
            block = next_block

        # Count chunks
        self._chunk_count = max(chunk_idx + 1, 0)
        self.chunks_recomputed.emit(self._chunk_count)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        """
        On mouse hover, find the block under the cursor and show a tooltip with the chunk number.
        """
        cursor = self.cursorForPosition(event.pos())
        block = cursor.block()
        idx = block.userState() if block.isValid() else -1

        # Only show tooltip for valid chunks
        if idx is not None and idx >= 0:
            # Avoid re-showing the same tooltip on every mouse move within the same chunk
            if self._last_hover_chunk != idx:
                self._last_hover_chunk = idx
                global_pt = self.mapToGlobal(event.pos())
                QtWidgets.QToolTip.showText(global_pt, f"Chunk #{idx + 1}", self)
        else:
            # Not in a chunk; hide tooltip if previously shown
            if self._last_hover_chunk is not None:
                QtWidgets.QToolTip.hideText()
                self._last_hover_chunk = None

        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent):
        # Hide tooltip when leaving the widget
        if self._last_hover_chunk is not None:
            QtWidgets.QToolTip.hideText()
            self._last_hover_chunk = None
        super().leaveEvent(event)

    def chunk_count(self) -> int:
        return self._chunk_count


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Interactive Patch Helper (with hover chunks)")
        self.resize(1000, 700)

        # Central layout
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        self.setCentralWidget(central)

        # Top controls: Root directory chooser
        top_row = QtWidgets.QHBoxLayout()
        layout.addLayout(top_row)

        self.root_edit = QtWidgets.QLineEdit(os.getcwd())
        self.root_edit.setReadOnly(True)
        choose_btn = QtWidgets.QPushButton("Choose Root…")
        choose_btn.clicked.connect(self.choose_root)

        top_row.addWidget(QtWidgets.QLabel("Root:"))
        top_row.addWidget(self.root_edit, stretch=1)
        top_row.addWidget(choose_btn)

        # Large text area for patch text with chunk-aware hover tooltips
        self.patch_edit = ChunkedPlainTextEdit()
        self.patch_edit.setPlaceholderText("Paste patch text here…")
        self.patch_edit.chunks_recomputed.connect(self._update_chunk_status)

        layout.addWidget(self.patch_edit, stretch=1)

        # Status bar
        self.statusBar().showMessage("Ready")

    def choose_root(self):
        current = self.root_edit.text() or os.getcwd()
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Root Directory", current)
        if directory:
            self.root_edit.setText(directory)
            self.statusBar().showMessage(f"Root directory set to: {directory}", 3000)

    @QtCore.Slot(int)
    def _update_chunk_status(self, n: int):
        self.statusBar().showMessage(f"Detected {n} chunk(s)")

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
