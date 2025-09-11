import sys
import os
from PySide6 import QtWidgets, QtCore, QtGui
from chunked_editor import ChunkedPlainTextEdit

# App identity for QSettings
QtCore.QCoreApplication.setOrganizationName("Grant")
QtCore.QCoreApplication.setOrganizationDomain("grantech.co")
QtCore.QCoreApplication.setApplicationName("InteractivePatchHelper")


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
        self.debug_check.stateChanged.connect(self._toggle_debug)
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
        self.patch_edit.chunkHovered.connect(self._on_chunk_hovered)

        # Right: File viewer
        self.file_viewer_edit = QtWidgets.QPlainTextEdit()
        self.file_viewer_edit.setReadOnly(True)
        self.file_viewer_edit.setPlaceholderText("Hover over a chunk on the left to see the corresponding file here.")
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.file_viewer_edit.setFont(fixed_font)

        splitter.addWidget(self.patch_edit)
        splitter.addWidget(self.file_viewer_edit)
        splitter.setSizes([600, 600])

        # Status bar
        self.statusBar().showMessage("Ready")

        # Load persisted settings
        self.load_settings()

    @QtCore.Slot(int, str, int)
    def _on_chunk_hovered(self, chunk_idx: int, file_path: str, start_line: int):
        """Loads file content and scrolls to the correct line."""
        if chunk_idx == -1 or not file_path:
            # Don't clear the viewer, just stop updating
            return

        root_dir = self.root_edit.text()
        if not root_dir:
            self.file_viewer_edit.setPlainText("ERROR: Root directory is not set.\n\nPlease choose a root directory first.")
            return

        full_path = os.path.join(root_dir, file_path)

        # Avoid reloading if the file is already displayed
        current_path = self.file_viewer_edit.property("current_file")
        if current_path != full_path:
            try:
                if os.path.isfile(full_path):
                    with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    self.file_viewer_edit.setPlainText(content)
                    self.file_viewer_edit.setProperty("current_file", full_path)
                    self.statusBar().showMessage(f"Showing: {file_path}", 4000)
                else:
                    self.file_viewer_edit.setPlainText(
                        f"File not found at the specified path.\n\n"
                        f"Root: {root_dir}\n"
                        f"File: {file_path}\n"
                        f"Full Path: {full_path}"
                    )
                    self.file_viewer_edit.setProperty("current_file", None)
                    self.statusBar().showMessage(f"File not found: {file_path}", 4000)
            except Exception as e:
                self.file_viewer_edit.setPlainText(f"Error reading file: {full_path}\n\n{str(e)}")
                self.file_viewer_edit.setProperty("current_file", None)
                self.statusBar().showMessage(f"Error reading {file_path}", 4000)
        
        # Now, scroll to the line
        if start_line > 0:
            self._scroll_to_line(self.file_viewer_edit, start_line)

    def _scroll_to_line(self, editor: QtWidgets.QPlainTextEdit, line_number: int):
        """Scrolls the editor to make a specific line visible."""
        doc = editor.document()
        if line_number > doc.blockCount():
            return

        # Line numbers are 1-based, block numbers are 0-based
        block = doc.findBlockByNumber(line_number - 1)
        if block.isValid():
            cursor = editor.textCursor()
            cursor.setPosition(block.position())
            editor.setTextCursor(cursor) # This also ensures the cursor is visible

    def choose_root(self):
        current = self.root_edit.text() or os.getcwd()
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Root Directory", current)
        if directory:
            self.root_edit.setText(directory)
            self.statusBar().showMessage(f"Root directory set to: {directory}", 3000)

    def _toggle_debug(self, state: int):
        on = state == QtCore.Qt.Checked
        self.patch_edit.set_debug(on)
        self.statusBar().showMessage("Debug logging " + ("enabled" if on else "disabled"), 2000)

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
        if geom: self.restoreGeometry(geom)
        if state: self.restoreState(state)
        root = s.value("app/rootDir", os.getcwd(), type=str)
        self.root_edit.setText(root)
        text = s.value("app/patchText", "", type=str)
        if text: self.patch_edit.setPlainText(text)

    def save_settings(self):
        s = QtCore.QSettings()
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/state", self.saveState())
        s.setValue("app/rootDir", self.root_edit.text())
        s.setValue("app/patchText", self.patch_edit.toPlainText())
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
