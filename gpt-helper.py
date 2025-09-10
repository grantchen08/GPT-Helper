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
logger.setLevel(logging.INFO)  # set to logging.DEBUG to see detailed logs


class ChunkedPlainTextEdit(QtWidgets.QPlainTextEdit):
    """
    - Splits text into chunks (file-level sections) for unified diffs.
      - Chunk starts at 'diff --git ...' or at '--- ...' followed by '+++ ...'
      - Never uses blank-line splitting if unified diff headers are present.
      - If no diff headers are present, uses blank-line fallback.
    - On hover: shows "Chunk #n" tooltip.
      - Tries to highlight (green) the first removal '-' run and up to N context lines ' ' before it within the
        first @@ hunk.
      - If not found, highlights the whole chunk (yellow).
    - Includes detailed debug logging.
    """
    chunks_recomputed = QtCore.Signal(int)

    def __init__(self, parent=None, debug=False, context_before=3):
        super().__init__(parent)
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.setFont(fixed_font)
        self.setMouseTracking(True)
        self.document().contentsChanged.connect(self._on_contents_changed)

        self._debug = debug
        self._context_before = max(1, min(3, context_before))
        self._chunk_count = 0
        self._last_hover_chunk = None

        # Precomputed per-chunk data
        self._chunk_spans = []         # list[(start_pos, end_pos_exclusive)]
        self._chunk_block_spans = []   # list[(start_block_num, end_block_num)]
        self._fuzzy_cache = {}         # dict[chunk_idx] -> (start_pos, end_pos_excl) or None

        # Formats
        self._fmt_whole_chunk = self._make_bg_format(QtGui.QColor(255, 235, 128, 120))  # yellow
        self._fmt_fuzzy_green = self._make_bg_format(QtGui.QColor(128, 255, 170, 140))  # green

        self._recompute_chunks()

    # -------- Unified diff detection and chunk starts --------

    @staticmethod
    def _is_unified_file_header(line: str, next_line: str | None) -> bool:
        """Return True if (line,next_line) look like a unified diff file header."""
        ls = line.lstrip()
        if ls.startswith("diff --git "):
            return True
        if ls.startswith("--- ") and next_line is not None and next_line.lstrip().startswith("+++ "):
            return True
        return False

    def _scan_has_unified_headers(self) -> bool:
        """Check once per recompute if any file headers exist."""
        doc = self.document()
        b = doc.firstBlock()
        while b.isValid():
            nxt = b.next()
            if self._is_unified_file_header(b.text(), nxt.text() if nxt.isValid() else None):
                return True
            b = nxt
        return False

    # -------- Content change and recompute --------

    def _on_contents_changed(self):
        self._fuzzy_cache.clear()
        self._recompute_chunks()
        if self._last_hover_chunk is not None and 0 <= self._last_hover_chunk < self._chunk_count:
            self._apply_hover_highlight(self._last_hover_chunk)
        else:
            self._clear_highlight()

    def _recompute_chunks(self):
        doc = self.document()
        self._chunk_spans.clear()
        self._chunk_block_spans.clear()
        self._fuzzy_cache.clear()

        # Reset states
        self._for_each_block(lambda b: b.setUserState(-1))

        has_unified_headers = self._scan_has_unified_headers()
        if self._debug:
            logger.debug("Recompute: has_unified_headers=%s", has_unified_headers)

        chunk_idx = -1
        current_chunk_start_block = None

        b = doc.firstBlock()
        while b.isValid():
            text = b.text()
            nxt = b.next()
            nxt_text = nxt.text() if nxt.isValid() else None

            start_reason = None

            if has_unified_headers:
                # Start chunks ONLY at file headers in unified mode
                if self._is_unified_file_header(text, nxt_text):
                    start_reason = "unified-header"
            else:
                # No headers at all: fallback mode
                # Start a new chunk at the first non-empty line or after a blank line preceding non-empty
                if chunk_idx == -1 and text.strip():
                    start_reason = "fallback-first-nonempty"
                elif text.strip() and b.previous().isValid() and not b.previous().text().strip():
                    start_reason = "fallback-blank-separator"

            if start_reason is not None:
                # Close previous chunk if open
                if chunk_idx >= 0 and current_chunk_start_block is not None:
                    self._append_chunk_span(current_chunk_start_block, b.previous())
                chunk_idx += 1
                current_chunk_start_block = b
                if self._debug:
                    logger.debug("Chunk %d starts at block %d (reason=%s)", chunk_idx, b.blockNumber(), start_reason)

            # tag block with current chunk (if any)
            b.setUserState(chunk_idx if chunk_idx >= 0 else -1)
            b = nxt

        # Close last chunk
        if chunk_idx >= 0 and current_chunk_start_block is not None:
            self._append_chunk_span(current_chunk_start_block, doc.lastBlock())

        self._chunk_count = max(chunk_idx + 1, 0)
        if self._debug:
            logger.debug("Chunks total: %d", self._chunk_count)
            for i, ((ps, pe), (bs, be)) in enumerate(zip(self._chunk_spans, self._chunk_block_spans)):
                logger.debug("  Chunk %d: pos=[%d,%d) blocks=[%d,%d]", i, ps, pe, bs, be)

        self.chunks_recomputed.emit(self._chunk_count)

    def _append_chunk_span(self, start_block: QtGui.QTextBlock, end_block: QtGui.QTextBlock):
        doc = self.document()
        start_pos = start_block.position()
        end_pos_excl = end_block.position() + len(end_block.text())  # no newline assumption

        # Clamp to [0, doc.characterCount()-1]
        doc_max = max(0, doc.characterCount() - 1)
        end_pos_excl = min(max(end_pos_excl, start_pos), doc_max)

        self._chunk_spans.append((start_pos, end_pos_excl))
        self._chunk_block_spans.append((start_block.blockNumber(), end_block.blockNumber()))

    # -------- Fuzzy green subspan detection --------

    def _get_fuzzy_subspan(self, chunk_idx: int):
        """
        Within the first @@ hunk of the chunk:
          - find the first '-' removal line;
          - include up to N preceding ' ' context lines (same hunk);
          - include the contiguous run of '-' lines;
        Return (start_pos, end_pos_excl) or None.
        """
        if chunk_idx in self._fuzzy_cache:
            return self._fuzzy_cache[chunk_idx]

        if chunk_idx < 0 or chunk_idx >= len(self._chunk_block_spans):
            self._fuzzy_cache[chunk_idx] = None
            return None

        doc = self.document()
        b_start, b_end = self._chunk_block_spans[chunk_idx]

        # Find first @@ hunk header in this chunk
        b = doc.findBlockByNumber(b_start)
        in_hunk = False
        context = deque(maxlen=self._context_before)
        removal_start = None
        removal_end = None
        context_blocks = []

        while b.isValid() and b.blockNumber() <= b_end:
            t = b.text()
            if t.startswith('@@'):
                in_hunk = True
                context.clear()
                if self._debug:
                    logger.debug("Chunk %d: hunk header at block %d", chunk_idx, b.blockNumber())
                b = b.next()
                continue

            if not in_hunk:
                b = b.next()
                continue

            if t.startswith(' '):
                context.append(b)
            elif t.startswith('-'):
                if removal_start is None:
                    context_blocks = list(context)
                    removal_start = b
                removal_end = b
            elif t.startswith('+'):
                if removal_start is not None:
                    break  # end of contiguous '-' run
                context.clear()
            else:
                # non unified markers inside hunk; treat as boundary
                if removal_start is not None:
                    break
                context.clear()

            b = b.next()

        if removal_start is None:
            self._fuzzy_cache[chunk_idx] = None
            if self._debug:
                logger.debug("Chunk %d: no removal '-' found in first hunk", chunk_idx)
            return None

        first_block = context_blocks[0] if context_blocks else removal_start
        last_block = removal_end or removal_start

        start_pos = first_block.position()
        end_pos_excl = last_block.position() + len(last_block.text())

        # Clamp to valid range
        doc_max = max(0, doc.characterCount() - 1)
        start_pos = min(max(0, start_pos), doc_max)
        end_pos_excl = min(max(0, end_pos_excl), doc_max)
        if end_pos_excl < start_pos:
            end_pos_excl = start_pos

        if self._debug:
            logger.debug(
                "Chunk %d: fuzzy span blocks ctx=%s removal=[%d..%d] -> pos=[%d,%d)",
                chunk_idx,
                [bb.blockNumber() for bb in context_blocks],
                removal_start.blockNumber(),
                last_block.blockNumber(),
                start_pos, end_pos_excl
            )

        self._fuzzy_cache[chunk_idx] = (start_pos, end_pos_excl)
        return self._fuzzy_cache[chunk_idx]

    # -------- Highlighting and events --------

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
        if chunk_idx < 0 or chunk_idx >= len(self._chunk_spans):
            self._clear_highlight()
            return

        fuzzy = self._get_fuzzy_subspan(chunk_idx)
        if fuzzy is not None:
            self._apply_selection(fuzzy[0], fuzzy[1], self._fmt_fuzzy_green)
        else:
            start_pos, end_pos_excl = self._chunk_spans[chunk_idx]
            self._apply_selection(start_pos, end_pos_excl, self._fmt_whole_chunk)

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

    # -------- Utilities --------

    def _for_each_block(self, fn):
        b = self.document().firstBlock()
        while b.isValid():
            fn(b)
            b = b.next()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Interactive Patch Helper (unified diff chunking fixed)")
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

        self.debug_check = QtWidgets.QCheckBox("Debug logs")
        self.debug_check.stateChanged.connect(self._toggle_debug)

        top_row.addWidget(QtWidgets.QLabel("Root:"))
        top_row.addWidget(self.root_edit, stretch=1)
        top_row.addWidget(choose_btn)
        top_row.addWidget(self.debug_check)

        # Editor
        self.patch_edit = ChunkedPlainTextEdit(debug=False, context_before=3)
        self.patch_edit.setPlaceholderText("Paste patch text here…")
        layout.addWidget(self.patch_edit, stretch=1)

        self.statusBar().showMessage("Ready")

    def _toggle_debug(self, state: int):
        on = state == QtCore.Qt.Checked
        logger.setLevel(logging.DEBUG if on else logging.INFO)
        self.patch_edit._debug = on
        # Force recompute to see logs about chunk detection
        self.patch_edit._on_contents_changed()
        self.statusBar().showMessage("Debug logging " + ("enabled" if on else "disabled"), 2000)

    def choose_root(self):
        current = self.root_edit.text() or os.getcwd()
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Root Directory", current)
        if directory:
            self.root_edit.setText(directory)
            self.statusBar().showMessage(f"Root directory set to: {directory}", 3000)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
