# chunked_editor.py
from collections import deque
from PySide6 import QtWidgets, QtCore, QtGui


class ChunkedPlainTextEdit(QtWidgets.QPlainTextEdit):
    """
    Chunk definition (unified diff semantics):
      - Each chunk is a run of contiguous '+' lines (additions).
      - If that '+' run is immediately preceded by a contiguous run of '-' lines (removals),
        those '-' lines are included in the same chunk.
      - Include up to N (1..3) preceding non-blank context lines (lines starting with a single space)
        before the '-'/'+' run. Blank context lines (a single ' ' with no content) are ignored.
        We collect these by scanning backwards from the first data line, so a blank line just before
        the '+' run no longer prevents including earlier non-empty context lines.

    Behavior:
      - Assigns a chunk index to every block in a chunk (block.userState = chunk_idx, 0-based).
      - On hover: shows "Chunk #n" and highlights the chunk in green.
      - Blocks not in any chunk: userState = -1, no tooltip/highlight.

    Notes:
      - Only considers content inside unified diff hunks (between lines beginning with '@@').
      - File headers ('diff --git', '---'/'+++') are ignored for chunk formation.
    """
    chunks_recomputed = QtCore.Signal(int)

    def __init__(self, parent=None, context_before=3, debug=False):
        super().__init__(parent)
        # Monospace font
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.setFont(fixed_font)
        self.setMouseTracking(True)

        # Settings
        self._context_before = max(1, min(3, int(context_before)))
        self._debug = bool(debug)

        # Data
        self._chunk_count = 0
        self._chunk_block_spans = []   # list of (start_block_num, end_block_num) inclusive
        self._chunk_pos_spans = []     # list of (start_pos, end_pos_exclusive)
        self._last_hover_chunk = None

        # Formats
        self._fmt_chunk_green = self._make_bg_format(QtGui.QColor(128, 255, 170, 140))  # green

        # Recompute when text changes
        self.document().contentsChanged.connect(self._recompute_chunks)
        self._recompute_chunks()

    # ---------- Public debug toggle ----------
    def set_debug(self, on: bool):
        self._debug = bool(on)
        self._recompute_chunks()

    # ---------- Helpers ----------
    def _make_bg_format(self, color: QtGui.QColor) -> QtGui.QTextCharFormat:
        fmt = QtGui.QTextCharFormat()
        fmt.setBackground(QtGui.QBrush(color))
        return fmt

    def _for_each_block(self):
        b = self.document().firstBlock()
        while b.isValid():
            yield b
            b = b.next()

    @staticmethod
    def _is_hunk_header(text: str) -> bool:
        return text.startswith('@@')

    @staticmethod
    def _is_add(text: str) -> bool:
        # unified diff addition lines but not "+++ filename" header
        return text.startswith('+') and not text.startswith('+++')

    @staticmethod
    def _is_del(text: str) -> bool:
        # unified diff deletion lines but not "--- filename" header
        return text.startswith('-') and not text.startswith('---')

    @staticmethod
    def _is_ctx(text: str) -> bool:
        return text.startswith(' ')

    @staticmethod
    def _ctx_has_content(text: str) -> bool:
        # Non-blank context means there is any non-whitespace after the leading space
        return text.startswith(' ') and len(text[1:].strip()) > 0

    def _collect_preceding_context_blocks(self, first_data_block: QtGui.QTextBlock, limit: int):
        """
        Walk backward from first_data_block and collect up to 'limit' previous context blocks that are non-blank.
        - Skips blank context lines (a ' ' followed by only whitespace).
        - Stops if it encounters a hunk header (@@) or a non-context line.
        Returns a list in forward order (oldest to newest).
        """
        out = []
        b = first_data_block.previous()
        while b.isValid() and len(out) < limit:
            t = b.text()
            if self._is_hunk_header(t):
                break
            if self._is_ctx(t):
                if self._ctx_has_content(t):
                    # prepend to keep forward order
                    out.insert(0, b)
                    b = b.previous()
                    continue
                else:
                    # blank context -> skip it but continue looking further back
                    b = b.previous()
                    continue
            # Not a context line -> stop
            break
        return out

    # ---------- Chunking core ----------
    def _recompute_chunks(self):
        doc = self.document()
        # Reset states
        for b in self._for_each_block():
            b.setUserState(-1)

        self._chunk_block_spans.clear()
        self._chunk_pos_spans.clear()

        # Scan per hunk; only lines between '@@' headers are considered
        in_hunk = False
        # We still keep a forward context window for efficiency, but final inclusion uses backward scan
        context_window = deque(maxlen=self._context_before)

        b = doc.firstBlock()
        while b.isValid():
            t = b.text()

            if self._is_hunk_header(t):
                in_hunk = True
                context_window.clear()
                b = b.next()
                continue

            if not in_hunk:
                b = b.next()
                continue

            # Track context lines; blank ones are ignored for final inclusion
            if self._is_ctx(t):
                if self._ctx_has_content(t):
                    context_window.append(b)
                else:
                    # Keep moving; we no longer clear the window hard because we’ll do a backward scan anyway
                    pass
                b = b.next()
                continue

            # We form a chunk when we find a '+' run; include a preceding '-' run if present,
            # and always include up to N immediately preceding non-empty context lines (backward scan).
            if self._is_del(t) or self._is_add(t):
                start_search_block = b

                # Optional preceding '-' run
                minus_start = None
                minus_end = None
                cur = start_search_block
                while cur.isValid() and self._is_del(cur.text()):
                    if minus_start is None:
                        minus_start = cur
                    minus_end = cur
                    cur = cur.next()

                # '+' run (must exist to form a chunk)
                plus_start_block = cur if minus_start is not None else start_search_block
                if plus_start_block.isValid() and self._is_add(plus_start_block.text()):
                    plus_start = plus_start_block
                    plus_end = plus_start_block
                    curp = plus_start_block.next()
                    while curp.isValid() and self._is_add(curp.text()):
                        plus_end = curp
                        curp = curp.next()

                    # First data line: either start of '-' run or start of '+' run
                    first_data_block = minus_start if minus_start is not None else plus_start

                    # NEW: backward collect up to N non-empty context lines (skip blank context)
                    context_blocks = self._collect_preceding_context_blocks(first_data_block, self._context_before)

                    # Chunk span in blocks (inclusive)
                    chunk_start_block = context_blocks[0] if context_blocks else first_data_block
                    chunk_end_block = plus_end

                    self._chunk_block_spans.append((chunk_start_block.blockNumber(), chunk_end_block.blockNumber()))

                    # Advance to after '+' run; reset forward window
                    b = curp
                    context_window.clear()
                    continue
                else:
                    # We saw '-' but no following '+': not a chunk; skip past '-' run
                    if minus_start is not None:
                        b = (minus_end.next() if minus_end is not None else b.next())
                        context_window.clear()
                        continue
                    # Unexpected line inside hunk; end hunk
                    in_hunk = False
                    context_window.clear()
                    b = b.next()
                    continue

            # Any other line ends the hunk
            in_hunk = False
            context_window.clear()
            b = b.next()

        # Convert block spans to positions and tag blocks
        for idx, (bn_start, bn_end) in enumerate(self._chunk_block_spans):
            start_block = doc.findBlockByNumber(bn_start)
            end_block = doc.findBlockByNumber(bn_end)
            start_pos = start_block.position()
            end_pos_excl = end_block.position() + len(end_block.text())

            # Clamp selection to valid range [0, characterCount()-1]
            doc_max = max(0, doc.characterCount() - 1)
            end_pos_excl = min(max(end_pos_excl, start_pos), doc_max)

            self._chunk_pos_spans.append((start_pos, end_pos_excl))

            # Tag blocks in chunk with the chunk index
            btag = start_block
            while btag.isValid() and btag.blockNumber() <= bn_end:
                btag.setUserState(idx)
                btag = btag.next()

        self._chunk_count = len(self._chunk_block_spans)
        self.chunks_recomputed.emit(self._chunk_count)

    # ---------- Highlight and events ----------
    def _clear_highlight(self):
        self.setExtraSelections([])

    def _apply_chunk_highlight(self, chunk_idx: int):
        if chunk_idx < 0 or chunk_idx >= len(self._chunk_pos_spans):
            self._clear_highlight()
            return
        doc = self.document()
        doc_max = max(0, doc.characterCount() - 1)
        start_pos, end_pos_excl = self._chunk_pos_spans[chunk_idx]
        s = min(max(0, start_pos), doc_max)
        e = min(max(0, end_pos_excl), doc_max)

        sel = QtWidgets.QTextEdit.ExtraSelection()
        sel.format = self._fmt_chunk_green
        sel.cursor = self.textCursor()
        sel.cursor.setPosition(s)
        sel.cursor.setPosition(e, QtGui.QTextCursor.KeepAnchor)
        self.setExtraSelections([sel])

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        cursor = self.cursorForPosition(event.pos())
        block = cursor.block()
        idx = block.userState() if block.isValid() else -1

        global_pt = self.mapToGlobal(event.pos())
        if idx is not None and idx >= 0:
            if self._last_hover_chunk != idx:
                self._last_hover_chunk = idx
                QtWidgets.QToolTip.showText(global_pt, f"Chunk #{idx + 1}", self)
            self._apply_chunk_highlight(idx)
        else:
            self._last_hover_chunk = None
            QtWidgets.QToolTip.hideText()
            self._clear_highlight()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent):
        self._last_hover_chunk = None
        QtWidgets.QToolTip.hideText()
        self._clear_highlight()
        super().leaveEvent(event)

    def chunk_count(self) -> int:
        return self._chunk_count
