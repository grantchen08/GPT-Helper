import sys
import os
import logging
from collections import deque
from PySide6 import QtWidgets, QtCore, QtGui

# Logging setup
logger = logging.getLogger("ChunkedPatch")
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)  # Set to logging.DEBUG to enable verbose logs


class ChunkedPlainTextEdit(QtWidgets.QPlainTextEdit):
    """
    QPlainTextEdit that:
    - Splits text into chunks (diff/file sections) and tags blocks with chunk index.
    - On hover: shows "Chunk #n" tooltip.
      - Tries to highlight (green) the first removal '-' run and up to N preceding context lines ' ' within
        the first @@ hunk of the chunk.
      - If not found, highlights whole chunk (yellow) like before.
    - Provides debug logging.
    """
    chunks_recomputed = QtCore.Signal(int)

    def __init__(self, parent=None, debug=False, context_before=3):
        super().__init__(parent)

        # Monospace font
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.setFont(fixed_font)

        self.setMouseTracking(True)
        self.document().contentsChanged.connect(self._on_contents_changed)

        self._chunk_count = 0
        self._last_hover_chunk = None
        self._debug = debug

        # Precomputed chunk spans: list of (start_pos, end_pos_exclusive)
        self._chunk_spans = []  # positions in document
        # Chunk block number spans: list of (start_block_number, end_block_number)
        self._chunk_block_spans = []

        # Cache for fuzzy subspans: dict chunk_idx -> (start_pos, end_pos_exclusive) or None
        self._fuzzy_cache = {}

        # Formats
        self._format_whole_chunk = self._make_bg_format(QtGui.QColor(255, 235, 128, 120))  # yellow
        self._format_fuzzy_green = self._make_bg_format(QtGui.QColor(128, 255, 170, 140))  # green

        # How many context lines before removal (1..3)
        self._context_before = max(1, min(3, context_before))

        self._recompute_chunks()

    @staticmethod
    def _is_chunk_start(line: str, next_line: str | None) -> bool:
        ls = line.lstrip()
        if ls.startswith("diff --git "):
            return True
        if ls.startswith("Index: "):
            return True
        if ls.startswith("*** "):
            return True
        if ls.startswith("--- ") and next_line is not None and next_line.lstrip().startswith("+++ "):
            return True
        return False

    def _on_contents_changed(self):
        # Clear caches and recompute
        self._fuzzy_cache.clear()
        self._recompute_chunks()
        # Re-apply highlight if still hovering a valid chunk
        if self._last_hover_chunk is not None and 0 <= self._last_hover_chunk < self._chunk_count:
            self._apply_hover_highlight(self._last_hover_chunk)
        else:
            self._clear_highlight()

    def _recompute_chunks(self):
        doc = self.document()
        block = doc.firstBlock()

        self._chunk_spans = []
        self._chunk_block_spans = []
        self._fuzzy_cache.clear()

        # Reset all userState
        self._for_each_block(lambda b: b.setUserState(-1))

        chunk_idx = -1
        prev_was_empty = True
        current_chunk_start_block = None

        while block.isValid():
            text = block.text()
            next_block = block.next()
            next_text = next_block.text() if next_block.isValid() else None

            is_start = self._is_chunk_start(text, next_text)

            # fallback: if in a chunk and we see a non-empty line after an empty line, treat as new chunk
            if not is_start and chunk_idx >= 0 and prev_was_empty and text.strip():
                is_start = True

            if chunk_idx == -1 and text.strip():
                is_start = True

            if is_start:
                # Close previous chunk
                if chunk_idx >= 0 and current_chunk_start_block is not None:
                    end_prev = block.previous()
                    self._append_chunk_span(current_chunk_start_block, end_prev)
                # Start new
                chunk_idx += 1
                current_chunk_start_block = block

            # Tag block
            block.setUserState(chunk_idx if chunk_idx >= 0 else -1)

            prev_was_empty = (len(text.strip()) == 0)
            block = next_block

        # Close last chunk
        if chunk_idx >= 0 and current_chunk_start_block is not None:
            last_block = doc.lastBlock()
            self._append_chunk_span(current_chunk_start_block, last_block)

        self._chunk_count = max(chunk_idx + 1, 0)

        if self._debug:
            logger.debug("Recomputed %d chunks", self._chunk_count)
            for i, ((ps, pe), (bs, be)) in enumerate(zip(self._chunk_spans, self._chunk_block_spans)):
                logger.debug("  Chunk %d: pos=[%d,%d) blocks=[%d,%d]", i, ps, pe, bs, be)

        self.chunks_recomputed.emit(self._chunk_count)

    def _append_chunk_span(self, start_block: QtGui.QTextBlock, end_block: QtGui.QTextBlock):
        doc = self.document()
        start_pos = start_block.position()
        # Exclusive end based on text length to avoid overshoot
        end_pos_excl = end_block.position() + len(end_block.text())

        # Clamp to valid range [0, characterCount()-1]
        doc_max = max(0, doc.characterCount() - 1)
        end_pos_excl = min(max(end_pos_excl, start_pos), doc_max)

        self._chunk_spans.append((start_pos, end_pos_excl))
        self._chunk_block_spans.append((start_block.blockNumber(), end_block.blockNumber()))

    def _make_bg_format(self, color: QtGui.QColor) -> QtGui.QTextCharFormat:
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtGui.QBrush(color))
        return fmt

    def _clear_highlight(self):
        if self._debug:
            logger.debug("Clearing highlight")
        self.setExtraSelections([])

    def _apply_selection(self, start_pos: int, end_pos_excl: int, fmt: QtGui.QTextCharFormat):
        doc = self.document()
        # clamp to [0, doc_max]
        doc_max = max(0, doc.characterCount() - 1)
        s = min(max(0, start_pos), doc_max)
        e = min(max(0, end_pos_excl), doc_max)
        if self._debug:
            logger.debug("Apply selection: start=%d end_excl=%d (doc_max=%d)", s, e, doc_max)

        sel = QtWidgets.QTextEdit.ExtraSelection()
        sel.format = fmt
        sel.cursor = self.textCursor()
        sel.cursor.setPosition(s)
        sel.cursor.setPosition(e, QtGui.QTextCursor.KeepAnchor)
        self.setExtraSelections([sel])

    def _apply_hover_highlight(self, chunk_idx: int):
        """
        Try to highlight the fuzzy green subspan for this chunk; fallback to full yellow chunk.
        """
        if chunk_idx < 0 or chunk_idx >= len(self._chunk_spans):
            self._clear_highlight()
            return

        fuzzy_span = self._get_fuzzy_subspan(chunk_idx)
        if fuzzy_span is not None:
            start_pos, end_pos_excl = fuzzy_span
            if self._debug:
                logger.debug("Chunk %d: highlighting fuzzy span [%d,%d)", chunk_idx, start_pos, end_pos_excl)
            self._apply_selection(start_pos, end_pos_excl, self._format_fuzzy_green)
        else:
            # Fallback to whole chunk
            start_pos, end_pos_excl = self._chunk_spans[chunk_idx]
            if self._debug:
                logger.debug("Chunk %d: fuzzy span not found, fallback to whole chunk [%d,%d)", chunk_idx, start_pos, end_pos_excl)
            self._apply_selection(start_pos, end_pos_excl, self._format_whole_chunk)

    def _get_fuzzy_subspan(self, chunk_idx: int):
        """
        Heuristic:
        - Within the first @@ hunk in the chunk, find the first '-' line and include:
          - up to N=1..3 preceding ' ' context lines (within the same hunk)
          - the contiguous run of '-' lines starting at that point
        - Return document positions [start, end_exclusive) for that subspan.
        - Cache result per chunk. Return None if no such run found.
        """
        if chunk_idx in self._fuzzy_cache:
            return self._fuzzy_cache[chunk_idx]

        doc = self.document()
        block_start, block_end = self._chunk_block_spans[chunk_idx]

        # Scan blocks
        in_hunk = False
        context_queue = deque(maxlen=self._context_before)

        # Track the first '-' run we find in the first hunk
        removal_start_block = None
        removal_end_block = None
        context_blocks = []

        b = doc.findBlockByNumber(block_start)
        # If the chunk starts before the first hunk, we search for the first '@@' to begin
        while b.isValid() and b.blockNumber() <= block_end:
            text = b.text()

            if text.startswith('@@'):
                # First hunk marker encountered
                in_hunk = True
                context_queue.clear()
                if self._debug:
                    logger.debug("Chunk %d: found hunk header at block %d", chunk_idx, b.blockNumber())
                b = b.next()
                continue

            if not in_hunk:
                b = b.next()
                continue

            # In hunk: track context and removals
            if text.startswith(' '):
                context_queue.append(b)
            elif text.startswith('-'):
                # start removal run at first '-' in this hunk
                if removal_start_block is None:
                    context_blocks = list(context_queue)  # up to N context lines
                    removal_start_block = b
                removal_end_block = b  # extend run
            elif text.startswith('+'):
                # additions end any contiguous '-' run
                if removal_start_block is not None:
                    break
                # else additions before any '-' are ignored with respect to our target
                context_queue.clear()
            else:
                # Any other content ends the hunk or is outside unified diff patterns
                if removal_start_block is not None:
                    break
                context_queue.clear()

            b = b.next()

        # If at end and we were in a removal run, it's valid
        if removal_start_block is not None and removal_end_block is None:
            removal_end_block = removal_start_block

        if removal_start_block is None:
            # No '-' found in the first hunk
            self._fuzzy_cache[chunk_idx] = None
            if self._debug:
                logger.debug("Chunk %d: no removal '-' found in first hunk", chunk_idx)
            return None

        # Compute positions for [start, end_excl)
        first_block = context_blocks[0] if context_blocks else removal_start_block
        last_block = removal_end_block

        start_pos = first_block.position()
        end_pos_excl = last_block.position() + len(last_block.text())

        # Safety clamp
        doc_max = max(0, doc.characterCount() - 1)
        start_pos = min(max(0, start_pos), doc_max)
        end_pos_excl = min(max(0, end_pos_excl), doc_max)
        if end_pos_excl < start_pos:
            end_pos_excl = start_pos

        if self._debug:
            ctx_nums = [b.blockNumber() for b in context_blocks]
            logger.debug(
                "Chunk %d: fuzzy span blocks ctx=%s removal=[%d..%d] -> pos=[%d,%d)",
                chunk_idx, ctx_nums,
                removal_start_block.blockNumber(), last_block.blockNumber(),
                start_pos, end_pos_excl
            )

        self._fuzzy_cache[chunk_idx] = (start_pos, end_pos_excl)
        return self._fuzzy_cache[chunk_idx]

    def _for_each_block(self, fn):
        doc = self.document()
        b = doc.firstBlock()
        while b.isValid():
            fn(b)
            b = b.next()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        cursor = self.cursorForPosition(event.pos())
        block = cursor.block()
        idx = block.userState() if block.isValid() else -1

        if self._debug:
            logger.debug(
                "Mouse move: pos=(%d,%d) block_valid=%s block_num=%s userState=%s",
                event.position().x(), event.position().y(),
                block.isValid(), block.blockNumber() if block.isValid() else None,
                idx if block.isValid() else None
            )

        # Tooltip and highlight handling
        global_pt = self.mapToGlobal(event.pos())
        if idx is not None and idx >= 0:
            if self._last_hover_chunk != idx:
                self._last_hover_chunk = idx
                QtWidgets.QToolTip.showText(global_pt, f"Chunk #{idx + 1}", self)
            self._apply_hover_highlight(idx)
        else:
            self._last_hover_chunk = None
            QtWidgets.QToolTip.hideText()
            self._clear_highlight()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent):
        if self._debug:
            logger.debug("Mouse left editor")
        self._last_hover_chunk = None
        QtWidgets.QToolTip.hideText()
        self._clear_highlight()
        super().leaveEvent(event)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Interactive Patch Helper (fuzzy green + fallback yellow)")
        self.resize(1000, 700)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        self.setCentralWidget(central)

        # Top controls
        top_row = QtWidgets.QHBoxLayout()
        layout.addLayout(top_row)

        self.root_edit = QtWidgets.QLineEdit(os.getcwd())
        self.root_edit.setReadOnly(True)
        choose_btn = QtWidgets.QPushButton("Choose Root…")
        choose_btn.clicked.connect(self.choose_root)

        # Debug checkbox
        self.debug_check = QtWidgets.QCheckBox("Debug logs")
        self.debug_check.stateChanged.connect(self._toggle_debug)

        top_row.addWidget(QtWidgets.QLabel("Root:"))
        top_row.addWidget(self.root_edit, stretch=1)
        top_row.addWidget(choose_btn)
        top_row.addWidget(self.debug_check)

        # Patch editor
        self.patch_edit = ChunkedPlainTextEdit(debug=False, context_before=3)
        self.patch_edit.setPlaceholderText("Paste patch text here…")
        self.patch_edit.chunks_recomputed.connect(self._update_chunk_status)

        layout.addWidget(self.patch_edit, stretch=1)

        self.statusBar().showMessage("Ready")

    def _toggle_debug(self, state: int):
        on = state == QtCore.Qt.Checked
        logger.setLevel(logging.DEBUG if on else logging.INFO)
        self.patch_edit._debug = on
        # Force recompute to get spans logged immediately
        self.patch_edit._on_contents_changed()
        self.statusBar().showMessage("Debug logging " + ("enabled" if on else "disabled"), 2000)

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
