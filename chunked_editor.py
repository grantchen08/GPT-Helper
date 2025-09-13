import re
from PySide6 import QtWidgets, QtCore, QtGui


class ChunkedPlainTextEdit(QtWidgets.QPlainTextEdit):
    """
    Chunk definition (unified diff semantics):
      - Each chunk is a run of contiguous '+' lines (additions).
      - If that '+' run is immediately preceded by a contiguous run of '-' lines (removals),
        those '-' lines are included in the same chunk.
      - Include up to N (1..3) preceding non-blank context lines.

    Behavior:
      - Assigns a chunk index to every block in a chunk (block.userState = chunk_idx, 0-based).
      - On hover: shows "Chunk #n", highlights the chunk, and emits a `chunkHovered` signal
        with the chunk's file path and its context lines for fuzzy matching.
      - Context menu: "Apply Chunk #n" emits chunkApplyRequested.
      - get_chunk_details(idx): returns details needed to apply a chunk.
    """
    chunks_recomputed = QtCore.Signal(int)
    # Emits (chunk_index, file_path, list_of_context_lines, first_context_block)
    chunkHovered = QtCore.Signal(int, str, list, QtGui.QTextBlock)
    # Emitted when the user chooses "Apply" via context menu on a chunk
    chunkApplyRequested = QtCore.Signal(int)

    def __init__(self, parent=None, context_before=3, debug=False):
        super().__init__(parent)
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.setFont(fixed_font)
        self.setMouseTracking(True)

        self._context_before = max(1, min(3, int(context_before)))
        self._debug = bool(debug)

        self._chunk_count = 0
        self._chunk_block_spans = []   # list[(bn_start, bn_end)]
        self._chunk_pos_spans = []     # list[(start_pos, end_pos_excl)]
        self._chunk_file_paths = []    # per-chunk file path
        self._chunk_context_info = []  # list[(context_lines, first_context_block)]
        self._last_hover_chunk = None

        self._fmt_chunk_green = self._make_bg_format(QtGui.QColor(128, 255, 170, 140))

        self.document().contentsChanged.connect(self._recompute_chunks)
        self._recompute_chunks()

    def set_debug(self, on: bool):
        self._debug = bool(on)
        self._recompute_chunks()

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
    def _is_new_file_header(text: str) -> bool:
        return text.startswith('+++ ')

    @staticmethod
    def _parse_filepath_from_header(text: str) -> str:
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return ""
        path_part = parts[1]
        return path_part[2:] if path_part.startswith('b/') else path_part

    @staticmethod
    def _is_hunk_header(text: str) -> bool:
        return text.startswith('@@')

    @staticmethod
    def _is_add(text: str) -> bool:
        return text.startswith('+') and not text.startswith('+++')

    @staticmethod
    def _is_del(text: str) -> bool:
        return text.startswith('-') and not text.startswith('---')

    @staticmethod
    def _is_ctx(text: str) -> bool:
        return text.startswith(' ')

    @staticmethod
    def _ctx_has_content(text: str) -> bool:
        return text.startswith(' ') and len(text[1:].strip()) > 0

    def _collect_preceding_context_blocks(self, first_data_block: QtGui.QTextBlock, limit: int):
        out = []
        b = first_data_block.previous()
        while b.isValid() and len(out) < limit:
            t = b.text()
            if self._is_hunk_header(t):
                break
            if self._is_ctx(t):
                if self._ctx_has_content(t):
                    out.insert(0, b)
                b = b.previous()
            else:
                break
        return out

    def _recompute_chunks(self):
        doc = self.document()
        for b in self._for_each_block():
            b.setUserState(-1)

        self._chunk_block_spans.clear()
        self._chunk_pos_spans.clear()
        self._chunk_file_paths.clear()
        self._chunk_context_info.clear()

        current_filepath = ""
        in_hunk = False

        b = doc.firstBlock()
        while b.isValid():
            t = b.text()

            if self._is_new_file_header(t):
                current_filepath = self._parse_filepath_from_header(t)
                in_hunk = False
                b = b.next()
                continue

            if self._is_hunk_header(t):
                in_hunk = True
                b = b.next()
                continue

            if not in_hunk:
                b = b.next()
                continue

            if self._is_del(t) or self._is_add(t):
                start_search_block = b
                minus_start, minus_end = None, None
                cur = start_search_block
                while cur.isValid() and self._is_del(cur.text()):
                    if minus_start is None:
                        minus_start = cur
                    minus_end = cur
                    cur = cur.next()

                plus_start_block = cur if minus_start is not None else start_search_block
                if plus_start_block.isValid() and self._is_add(plus_start_block.text()):
                    plus_start, plus_end = plus_start_block, plus_start_block
                    curp = plus_start_block.next()
                    while curp.isValid() and self._is_add(curp.text()):
                        plus_end = curp
                        curp = curp.next()

                    first_data_block = minus_start if minus_start is not None else plus_start
                    context_blocks = self._collect_preceding_context_blocks(first_data_block, self._context_before)
                    chunk_start_block = context_blocks[0] if context_blocks else first_data_block
                    chunk_end_block = plus_end

                    self._chunk_block_spans.append((chunk_start_block.blockNumber(), chunk_end_block.blockNumber()))
                    self._chunk_file_paths.append(current_filepath)

                    # Collect context lines and the first context block for this chunk
                    chunk_context_lines = []
                    first_context_block = None
                    iter_block = chunk_start_block
                    while iter_block.isValid() and iter_block.blockNumber() <= chunk_end_block.blockNumber():
                        if self._is_ctx(iter_block.text()):
                            chunk_context_lines.append(iter_block.text()[1:])
                            if first_context_block is None:
                                first_context_block = iter_block
                        iter_block = iter_block.next()
                    self._chunk_context_info.append((chunk_context_lines, first_context_block))

                    b = curp
                    continue
                else:
                    b = (minus_end.next() if minus_end is not None else b.next())
                    continue

            b = b.next()

        # Mark and store position spans
        for idx, (bn_start, bn_end) in enumerate(self._chunk_block_spans):
            start_block = doc.findBlockByNumber(bn_start)
            end_block = doc.findBlockByNumber(bn_end)
            start_pos = start_block.position()
            end_pos_excl = end_block.position() + len(end_block.text())
            self._chunk_pos_spans.append((start_pos, end_pos_excl))

            btag = start_block
            while btag.isValid() and btag.blockNumber() <= bn_end:
                btag.setUserState(idx)
                btag = btag.next()

        self._chunk_count = len(self._chunk_block_spans)
        self.chunks_recomputed.emit(self._chunk_count)

    def _clear_highlight(self):
        self.setExtraSelections([])

    def _apply_chunk_highlight(self, chunk_idx: int):
        if chunk_idx < 0 or chunk_idx >= len(self._chunk_pos_spans):
            self._clear_highlight()
            return
        start_pos, end_pos_excl = self._chunk_pos_spans[chunk_idx]
        sel = QtWidgets.QTextEdit.ExtraSelection()
        sel.format = self._fmt_chunk_green
        sel.cursor = self.textCursor()
        sel.cursor.setPosition(start_pos)
        sel.cursor.setPosition(end_pos_excl, QtGui.QTextCursor.KeepAnchor)
        self.setExtraSelections([sel])

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        cursor = self.cursorForPosition(event.pos())
        block = cursor.block()
        idx = block.userState() if block.isValid() else -1

        if idx is not None and idx >= 0:
            if self._last_hover_chunk != idx:
                self._last_hover_chunk = idx
                QtWidgets.QToolTip.showText(self.mapToGlobal(event.pos()), f"Chunk #{idx + 1}", self)
                filepath = self._chunk_file_paths[idx]
                context_lines, first_context_block = self._chunk_context_info[idx]
                self.chunkHovered.emit(idx, filepath, context_lines, first_context_block)
            self._apply_chunk_highlight(idx)
        else:
            if self._last_hover_chunk is not None:
                self.chunkHovered.emit(-1, "", [], None)
            self._last_hover_chunk = None
            QtWidgets.QToolTip.hideText()
            self._clear_highlight()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent):
        self._last_hover_chunk = None
        QtWidgets.QToolTip.hideText()
        self._clear_highlight()
        self.chunkHovered.emit(-1, "", [], None)
        super().leaveEvent(event)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent):
        # Determine if the cursor is over a chunk
        cursor = self.cursorForPosition(event.pos())
        block = cursor.block()
        idx = block.userState() if block.isValid() else -1

        if idx is None or idx < 0:
            return super().contextMenuEvent(event)

        menu = QtWidgets.QMenu(self)
        act_apply = menu.addAction(f"Apply Chunk #{idx + 1}")
        chosen = menu.exec(event.globalPos())
        if chosen == act_apply:
            self.chunkApplyRequested.emit(idx)

    def chunk_count(self) -> int:
        return self._chunk_count

    # NEW: Provide structured details for applying a chunk
    def get_chunk_details(self, chunk_idx: int):
        """
        Returns a dict with:
          file_path: str
          context_lines: list[str]
          n_context: int
          removed_lines: list[str]  # '-' lines without leading '-'
          added_lines: list[str]    # '+' lines without leading '+'
        """
        if chunk_idx < 0 or chunk_idx >= len(self._chunk_block_spans):
            return None

        file_path = self._chunk_file_paths[chunk_idx]
        context_lines, _first_ctx_block = self._chunk_context_info[chunk_idx]

        bn_start, bn_end = self._chunk_block_spans[chunk_idx]
        start_block = self.document().findBlockByNumber(bn_start)
        end_block = self.document().findBlockByNumber(bn_end)

        removed_lines = []
        added_lines = []

        b = start_block
        while b.isValid() and b.blockNumber() <= bn_end:
            t = b.text()
            if self._is_del(t):
                removed_lines.append(t[1:])
            elif self._is_add(t):
                added_lines.append(t[1:])
            b = b.next()

        return {
            "file_path": file_path,
            "context_lines": list(context_lines),
            "n_context": len(context_lines),
            "removed_lines": removed_lines,
            "added_lines": added_lines,
        }
