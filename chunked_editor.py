# chunked_editor.py
import re
from collections import deque
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
        with the chunk's file path and starting line number in the new file.
    """
    chunks_recomputed = QtCore.Signal(int)
    chunkHovered = QtCore.Signal(int, str, int)  # Emits (chunk_index, file_path, start_line)

    def __init__(self, parent=None, context_before=3, debug=False):
        super().__init__(parent)
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.setFont(fixed_font)
        self.setMouseTracking(True)

        self._context_before = max(1, min(3, int(context_before)))
        self._debug = bool(debug)

        self._chunk_count = 0
        self._chunk_block_spans = []
        self._chunk_pos_spans = []
        self._chunk_file_paths = []
        self._chunk_start_lines = []  # NEW: Starting line number for each chunk in the new file
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
        if len(parts) < 2: return ""
        path_part = parts[1]
        return path_part[2:] if path_part.startswith('b/') else path_part

    @staticmethod
    def _is_hunk_header(text: str) -> bool:
        return text.startswith('@@')

    @staticmethod
    def _parse_hunk_start_line(text: str) -> int:
        # Parses '@@ -1,5 +10,4 @@' -> 10
        match = re.search(r'\+(\d+)', text)
        return int(match.group(1)) if match else -1

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
            if self._is_hunk_header(t): break
            if self._is_ctx(t):
                if self._ctx_has_content(t): out.insert(0, b)
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
        self._chunk_start_lines.clear()

        current_filepath = ""
        hunk_header_block = None
        hunk_start_line_in_new_file = -1

        b = doc.firstBlock()
        while b.isValid():
            t = b.text()

            if self._is_new_file_header(t):
                current_filepath = self._parse_filepath_from_header(t)
                hunk_header_block = None
                b = b.next()
                continue

            if self._is_hunk_header(t):
                hunk_header_block = b
                hunk_start_line_in_new_file = self._parse_hunk_start_line(t)
                b = b.next()
                continue

            if hunk_header_block is None:
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

                    # Calculate the starting line number for this chunk
                    line_offset = 0
                    iter_block = hunk_header_block.next()
                    while iter_block.isValid() and iter_block.blockNumber() < chunk_start_block.blockNumber():
                        if not self._is_del(iter_block.text()):
                            line_offset += 1
                        iter_block = iter_block.next()
                    
                    chunk_line = hunk_start_line_in_new_file + line_offset

                    self._chunk_block_spans.append((chunk_start_block.blockNumber(), chunk_end_block.blockNumber()))
                    self._chunk_file_paths.append(current_filepath)
                    self._chunk_start_lines.append(chunk_line)

                    b = curp
                    continue
                else:
                    b = (minus_end.next() if minus_end is not None else b.next())
                    continue

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
                start_line = self._chunk_start_lines[idx]
                self.chunkHovered.emit(idx, filepath, start_line)
            self._apply_chunk_highlight(idx)
        else:
            if self._last_hover_chunk is not None:
                self.chunkHovered.emit(-1, "", -1)
            self._last_hover_chunk = None
            QtWidgets.QToolTip.hideText()
            self._clear_highlight()

        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QtCore.QEvent):
        self._last_hover_chunk = None
        QtWidgets.QToolTip.hideText()
        self._clear_highlight()
        self.chunkHovered.emit(-1, "", -1)
        super().leaveEvent(event)

    def chunk_count(self) -> int:
        return self._chunk_count
