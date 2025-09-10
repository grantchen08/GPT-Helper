import sys
import os
import logging
from PySide6 import QtWidgets, QtCore, QtGui

# Configure logging (set level to logging.DEBUG to see detailed logs)
logger = logging.getLogger("ChunkedPatch")
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)  # change to logging.DEBUG to enable verbose debug


class ChunkedPlainTextEdit(QtWidgets.QPlainTextEdit):
    """
    A QPlainTextEdit that:
    - Parses the document into "chunks" (diff/file sections) on text changes.
    - Shows a tooltip with the chunk number when hovering over a line belonging to a chunk.
    - Highlights the entire chunk under the mouse.
    - Provides debug logging to help diagnose chunk mapping and selection ranges.
    """
    chunks_recomputed = QtCore.Signal(int)  # emits total chunk count

    def __init__(self, parent=None, debug=False):
        super().__init__(parent)
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.setFont(fixed_font)

        self.setMouseTracking(True)
        self.document().contentsChanged.connect(self._on_contents_changed)

        self._chunk_count = 0
        self._last_hover_chunk = None
        self._hover_highlight_format = self._make_hover_format()

        # Precomputed spans per chunk: list of (start_pos_inclusive, end_pos_exclusive)
        self._chunk_spans = []  # type: list[tuple[int, int]]

        # Toggle runtime debug
        self._debug = debug

        self._recompute_chunks()

    @staticmethod
    def _is_chunk_start(line: str, next_line: str | None) -> bool:
        ls = line.lstrip()
        if ls.startswith("diff --git "):
            return True
        if ls.startswith("Index: "):
            return True
        if ls.startswith("*** "):  # context diff header
            return True
        if ls.startswith("--- ") and next_line is not None and next_line.lstrip().startswith("+++ "):
            return True
        return False

    def _on_contents_changed(self):
        self._recompute_chunks()
        # Re-apply highlight if we still have a valid hovered chunk
        if self._last_hover_chunk is not None and 0 <= self._last_hover_chunk < self._chunk_count:
            self._apply_chunk_highlight(self._last_hover_chunk)
        else:
            self._clear_chunk_highlight()

    def _recompute_chunks(self):
        """
        Re-tag each block with its chunk index (userState) and build a span list
        mapping chunk index to (start_pos, end_pos_exclusive) in document coordinates.
        """
        doc = self.document()
        block = doc.firstBlock()

        # Reset spans and states
        self._chunk_spans = []
        for b in self._iter_blocks():
            b.setUserState(-1)

        chunk_idx = -1
        prev_was_empty = True

        # Temporary tracking for span construction
        current_chunk_start_block = None

        while block.isValid():
            text = block.text()
            next_block = block.next()
            next_text = next_block.text() if next_block.isValid() else None

            is_start = self._is_chunk_start(text, next_text)

            # Fallback: split on blank-line-separated groups if already inside a chunk
            if not is_start and chunk_idx >= 0 and prev_was_empty and text.strip():
                is_start = True

            # If no chunk started yet and we see content, start first chunk
            if chunk_idx == -1 and text.strip():
                is_start = True

            if is_start:
                # Close previous chunk span
                if chunk_idx >= 0 and current_chunk_start_block is not None:
                    # end of previous chunk is previous block
                    end_block_prev = block.previous()
                    self._append_chunk_span(current_chunk_start_block, end_block_prev)
                # Start new chunk
                chunk_idx += 1
                current_chunk_start_block = block

            # Tag block with chunk index or -1
            if chunk_idx >= 0:
                block.setUserState(chunk_idx)
            else:
                block.setUserState(-1)

            prev_was_empty = (len(text.strip()) == 0)
            block = next_block

        # Close last chunk if open
        if chunk_idx >= 0 and current_chunk_start_block is not None:
            last_block = doc.lastBlock()
            self._append_chunk_span(current_chunk_start_block, last_block)

        self._chunk_count = max(chunk_idx + 1, 0)
        if self._debug:
            logger.debug("Recomputed chunks: count=%d", self._chunk_count)
            for i, (s, e) in enumerate(self._chunk_spans):
                logger.debug("  Chunk %d span: start=%d end_exclusive=%d len=%d",
                             i, s, e, max(0, e - s))
        self.chunks_recomputed.emit(self._chunk_count)

    def _append_chunk_span(self, start_block: QtGui.QTextBlock, end_block: QtGui.QTextBlock):
        """
        Compute and store the document position span [start, end_exclusive)
        for the chunk from start_block through end_block.
        """
        doc = self.document()
        # Inclusive start at the start of the first block
        start_pos = start_block.position()

        # For exclusive end, take the end of the last block's text (without assuming a trailing newline)
        # and clamp to the document's max valid position (characterCount() - 1).
        last_block_text_len = len(end_block.text())
        end_pos_exclusive = end_block.position() + last_block_text_len

        # Valid positions are [0, characterCount()-1]; the selection API accepts end_exclusive
        # but ensure we don't exceed the doc end.
        doc_max = max(0, doc.characterCount() - 1)
        if end_pos_exclusive > doc_max:
            # Allow selecting up to doc_max (exclusive end may equal doc_max)
            # QTextCursor.setPosition expects positions <= doc_max; for the exclusive end,
            # we will set keep-anchor to end_exclusive, but we need to clamp to doc_max.
            if self._debug:
                logger.debug("Clamping end_pos_exclusive from %d to %d (doc_max)", end_pos_exclusive, doc_max)
            end_pos_exclusive = doc_max

        # Guard against inverted ranges
        end_pos_exclusive = max(end_pos_exclusive, start_pos)

        self._chunk_spans.append((start_pos, end_pos_exclusive))

    def _make_hover_format(self) -> QtGui.QTextCharFormat:
        fmt = QtGui.QTextCharFormat()
        color = QtGui.QColor(255, 235, 128, 120)  # semi-transparent yellow
        fmt.setBackground(QtGui.QBrush(color))
        return fmt

    def _clear_chunk_highlight(self):
        if self._debug:
            logger.debug("Clearing highlight")
        self.setExtraSelections([])

    def _apply_chunk_highlight(self, chunk_idx: int):
        """
        Highlight the precomputed span for chunk_idx using QTextEdit.ExtraSelection.
        """
        if chunk_idx < 0 or chunk_idx >= len(self._chunk_spans):
            self._clear_chunk_highlight()
            return

        doc = self.document()
        doc_char_count = doc.characterCount()
        doc_max = max(0, doc_char_count - 1)

        start_pos, end_pos_exclusive = self._chunk_spans[chunk_idx]

        # Clamp positions to [0, doc_max]
        start_pos_clamped = min(max(0, start_pos), doc_max)
        end_pos_exclusive_clamped = min(max(0, end_pos_exclusive), doc_max)

        if self._debug:
            logger.debug(
                "Apply highlight for chunk %d: doc_chars=%d start=%d->%d end_excl=%d->%d",
                chunk_idx, doc_char_count, start_pos, start_pos_clamped,
                end_pos_exclusive, end_pos_exclusive_clamped
            )

        # Build selection; make sure selection length >= 0
        selection = QtWidgets.QTextEdit.ExtraSelection()
        selection.format = self._hover_highlight_format
        selection.cursor = self.textCursor()
        selection.cursor.setPosition(start_pos_clamped)
        selection.cursor.setPosition(end_pos_exclusive_clamped, QtGui.QTextCursor.KeepAnchor)

        self.setExtraSelections([selection])

    def _update_hover_chunk(self, idx: int | None, global_pt: QtCore.QPoint | None):
        """
        Update tooltip and highlight for the chunk under the cursor.
        """
        if idx is not None and idx >= 0:
            if self._last_hover_chunk != idx:
                self._last_hover_chunk = idx
                if self._debug:
                    logger.debug("Hover chunk changed -> %d", idx)
                if global_pt is not None:
                    QtWidgets.QToolTip.showText(global_pt, f"Chunk #{idx + 1}", self)
            self._apply_chunk_highlight(idx)
        else:
            if self._last_hover_chunk is not None and self._debug:
                logger.debug("Hover left any chunk")
            self._last_hover_chunk = None
            QtWidgets.QToolTip.hideText()
            self._clear_chunk_highlight()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        cursor = self.cursorForPosition(event.pos())
        block = cursor.block()
        idx = block.userState() if block.isValid() else -1

        if self._debug:
            logger.debug(
                "Mouse move: pos=(%d,%d) block_valid=%s block_num=%s userState=%s block_pos=%s",
                event.position().x(), event.position().y(),
                block.isValid(), block.blockNumber() if block.isValid() else None,
                idx if block.isValid() else None,
                block.position() if block.isValid() else None
            )

        global_pt = self.mapToGlobal(event.pos())
        self._update_hover_chunk(idx if idx is not None else -1, global_pt)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent):
        if self._last_hover_chunk is not None and self._debug:
            logger.debug("Mouse left editor")
        self._last_hover_chunk = None
        QtWidgets.QToolTip.hideText()
        self._clear_chunk_highlight()
        super().leaveEvent(event)

    def _iter_blocks(self):
        doc = self.document()
        block = doc.firstBlock()
        while block.isValid():
            yield block
            block = block.next()

    def chunk_count(self) -> int:
        return self._chunk_count


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Interactive Patch Helper (hover-highlight chunks + debug)")
        self.resize(1000, 700)

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

        # Debug toggle checkbox
        self.debug_check = QtWidgets.QCheckBox("Debug logs")
        self.debug_check.stateChanged.connect(self._toggle_debug)

        top_row.addWidget(QtWidgets.QLabel("Root:"))
        top_row.addWidget(self.root_edit, stretch=1)
        top_row.addWidget(choose_btn)
        top_row.addWidget(self.debug_check)

        # Patch editor
        self.patch_edit = ChunkedPlainTextEdit(debug=False)
        self.patch_edit.setPlaceholderText("Paste patch text here…")
        self.patch_edit.chunks_recomputed.connect(self._update_chunk_status)

        layout.addWidget(self.patch_edit, stretch=1)

        # Status bar
        self.statusBar().showMessage("Ready")

    def _toggle_debug(self, state: int):
        on = state == QtCore.Qt.Checked
        logger.setLevel(logging.DEBUG if on else logging.INFO)
        self.patch_edit._debug = on
        # Force recompute to print spans if turning on
        if on:
            self.patch_edit._recompute_chunks()
            self.statusBar().showMessage("Debug logging enabled", 2000)
        else:
            self.statusBar().showMessage("Debug logging disabled", 2000)

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
