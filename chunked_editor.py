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
      - On hover: shows "Chunk #n", highlights the chunk, and emits a `chunkHovered` signal
        with the chunk's file path.
      - Blocks not in any chunk: userState = -1, no tooltip/highlight.

    Notes:
      - Parses file paths from '+++ b/path/to/file' lines.
      - Only considers content inside unified diff hunks (between lines beginning with '@@').
    """
    chunks_recomputed = QtCore.Signal(int)
    chunkHovered = QtCore.Signal(int, str)  # Emits (chunk_index, file_path)

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
        self._chunk_file_paths = []    # list of file paths corresponding to each chunk
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
    def _is_new_file_header(text: str) -> bool:
        return text.startswith('+++ ')

    @staticmethod
    def _parse_filepath_from_header(text: str) -> str:
        # Parses '+++ b/path/to/file.py' -> 'path/to/file.py'
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return ""
        path_part = parts[1]
        if path_part.startswith('b/'):
            return path_part[2:]
        return path_part

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

    # ---------- Chunking core ----------
    def _recompute_chunks(self):
        doc = self.document()
        for b in self._for_each_block():
            b.setUserState(-1)

        self._chunk_block_spans.clear()
        self._chunk_pos_spans.clear()
        self._chunk_file_paths.clear()

        in_hunk = False
        current_filepath = ""
        context_window = deque(maxlen=self._context_before)

        b = doc.firstBlock()
        while b.isValid():
            t = b.text()

            if self._is_new_file_header(t):
                current_filepath = self._parse_filepath_from_header(t)
                in_hunk = False
                context_window.clear()
                b = b.next()
                continue

            if self._is_hunk_header(t):
                in_hunk = True
                context_window.clear()
                b = b.next()
                continue

            if not in_hunk:
                b = b.next()
                continue

            if self._is_ctx(t):
                if self._ctx_has_content(t):
                    context_window.append(b)
                b = b.next()
                continue

            if self._is_del(t) or self._is_add(t):
                start_search_block = b
                minus_start, minus_end = None, None
                cur = start_search_block
                while cur.isValid() and self._is_del(cur.text()):
                    if minus_start is None: minus_start = cur
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

                    b = curp
                    context_window.clear()
                    continue
                else:
                    if minus_start is not None:
                        b = (minus_end.next() if minus_end is not None else b.next())
                        context_window.clear()
                        continue
                    in_hunk = False
                    context_window.clear()
                    b = b.next()
                    continue

            in_hunk = False
            context_window.clear()
            b = b.next()

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

    # ---------- Highlight and events ----------
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
                filepath = self._chunk_file_paths[idx] if idx < len(self._chunk_file_paths) else ""
                self.chunkHovered.emit(idx, filepath)
            self._apply_chunk_highlight(idx)
        else:
            if self._last_hover_chunk is not None:
                self.chunkHovered.emit(-1, "")
            self._last_hover_chunk = None
            QtWidgets.QToolTip.hideText()
            self._clear_highlight()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent):
        self._last_hover_chunk = None
        QtWidgets.QToolTip.hideText()
        self._clear_highlight()
        self.chunkHovered.emit(-1, "")
        super().leaveEvent(event)

    def chunk_count(self) -> int:
        return self._chunk_count
