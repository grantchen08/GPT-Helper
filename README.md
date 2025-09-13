# Interactive Patch Helper (PySide6)

An interactive desktop tool to preview and apply individual patch chunks to files with helpful context, fuzzy matching, and a live diff preview. Paste a unified diff on the left, open/edit the target file on the right, and apply selected hunks to the in-memory buffer safely and transparently.

## Features

- Interactive chunk navigation
  - Parses unified diffs into discrete chunks, including:
    - Replacements (contiguous `-` lines followed by `+` lines)
    - Additions (contiguous `+` lines)
    - Deletions (contiguous `-` lines, pure deletions)
  - Hover a chunk to see where it would apply in the file, with contextual highlighting.
  - Apply a chunk via a button or the left-pane context menu.
- Fuzzy context matching
  - Uses `thefuzz` to locate chunk context within the target file, even when it has changed slightly.
  - Heuristics to detect “already applied” chunks.
- Live unified diff preview
  - Shows the before/after of the in-memory file buffer when hovering an applicable chunk.
- In-memory, safe editing
  - Changes are applied to the editor’s buffer; you decide how/when to save to disk.
- Helpful UI
  - Line-numbered code viewer (right pane).
  - Chunk-aware patch editor (left pane).
  - Bottom dock with unified diff preview.
  - Status messages, tooltips, and optional debug logging.
- Persistent settings
  - Remembers window layout, root directory, patch text, and debug flag via `QSettings`.
- Cross-platform
  - Works on Windows, macOS, and Linux (PySide6/Qt).

## How it works

- Left panel: paste a unified diff/patch. The editor identifies chunks:
  - Runs of `+` lines, optionally preceded by `-` lines (replacement/addition).
  - Runs of `-` lines alone (pure deletion).
  - Associates up to N (configurable, 1..3) preceding non-blank context lines to each chunk for matching.
- Right panel: loads the file referenced by the hovered chunk (if empty), or reuses the open buffer.
- Hover a chunk:
  - The app tries to locate it in the open file using fuzzy matching on context lines.
  - If found and applicable, it highlights the region, enables “Apply Hovered Chunk,” and shows the unified diff preview.
- Apply:
  - Replaces removal block with additions (or inserts/deletes accordingly) in-memory, highlights the change, and refreshes the preview.

## Project layout

- `gpt-helper.py`
  - Main application window, fuzzy matching, diff preview, in-memory apply.
- `chunked_editor.py`
  - Patch editor that parses unified diffs into hoverable/applyable chunks and exposes chunk metadata.

## Installation

- Python 3.9+ recommended.

```bash
pip install PySide6 thefuzz python-Levenshtein
```

Note: `python-Levenshtein` is optional but improves `thefuzz` performance.

## Running

```bash
python gpt-helper.py
```

## Basic usage

1) Choose root directory  
   Click “Choose Root…” and pick your repository/workspace root (used to resolve file paths in patches).

2) Paste a patch  
   Paste unified diff text into the left panel.

3) Hover a chunk  
   - The right panel loads the target file if empty and highlights where the chunk should apply.
   - The bottom dock shows a unified diff preview.

4) Apply  
   - Click “Apply Hovered Chunk” (or right-click a chunk in the left panel and choose Apply).
   - The change is applied to the in-memory file buffer only.

5) Save (manual)  
   - Use your editor/IDE or add a “Save” action to write the right-pane buffer to disk (not included by default).

## Chunk types supported

- Replacement: contiguous `-` lines followed by contiguous `+` lines.
- Addition: contiguous `+` lines (no removals).
- Deletion: contiguous `-` lines (pure deletion, no additions).

## Matching and application details

- Fuzzy matching
  - Sliding-window comparison of a chunk’s context block vs. the open file using `thefuzz.fuzz.ratio`.
  - A minimum score threshold (commonly ≥ 60 in hover evaluation) determines a valid match.

- Already-applied detection (heuristic)
  - If the “added” block exists at the expected location, and the “removed” block is not found nearby, the chunk is considered already applied.

- Disambiguation
  - If the “removed” block occurs multiple times, the app prefers the nearest to the matched context. You can refine context by editing the file.

## Extending the app

- Save to disk
  - Add a button to write the right-pane buffer to the file path stored in `current_file` (consider backups/atomic writes).

- Copy diff
  - Add a “Copy Diff” button to copy the bottom dock’s unified diff to the clipboard.

- Git integration
  - Load file contents for a specific revision, stage changes, or export applied chunks as a `.patch` file.

## Development

- Recommended environment: Python 3.9+, venv, Qt via PySide6.
- Run:
  ```bash
  python gpt-helper.py
  ```
- Lint/format:
  - Add your preferred tools (e.g., `ruff`, `black`, `isort`).
- Tests (suggested):
  - Parsing chunks (including pure deletions).
  - Matching (fuzzy and exact).
  - Apply logic (replacement, insertion, deletion).
  - Diff preview.

## Known limitations

- In-memory only
  - This tool doesn’t save changes to disk by default.
- Matching can be fooled by repeated/very similar contexts.
- Pure insertion with zero context can be ambiguous; the app requires context or a specific target to apply.

## License

Licensed under the BSD 3-Clause License - see the LICENSE file for details.

## Acknowledgements

- Built with PySide6/Qt.
- Fuzzy matching by `thefuzz` (with `python-Levenshtein` accelerator).
- Unified diff via Python’s `difflib`.

## Contributing

Issues and pull requests are welcome. Please include clear repro steps and, when possible, minimal patches and sample files.
