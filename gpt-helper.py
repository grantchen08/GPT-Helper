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
        self.setWindowTitle("Interactive Patch Helper (refactored)")
        self.resize(1000, 700)

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

        # Debug checkbox
        self.debug_check = QtWidgets.QCheckBox("Debug logs")
        self.debug_check.stateChanged.connect(self._toggle_debug)

        # Relaunch button
        self.relaunch_btn = QtWidgets.QPushButton("Relaunch")
        self.relaunch_btn.clicked.connect(self.relaunch_app)

        top_row.addWidget(QtWidgets.QLabel("Root:"))
        top_row.addWidget(self.root_edit, stretch=1)
        top_row.addWidget(choose_btn)
        top_row.addWidget(self.debug_check)
        top_row.addWidget(self.relaunch_btn)

        # Patch editor: chunking by contiguous '+' with optional preceding '-' and up to 3 context lines
        self.patch_edit = ChunkedPlainTextEdit(context_before=3, debug=False)
        self.patch_edit.setPlaceholderText("Paste patch text here…")
        layout.addWidget(self.patch_edit, stretch=1)

        # Status bar
        self.statusBar().showMessage("Ready")

        # Load persisted settings
        self.load_settings()

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
        # Save settings first
        try:
            self.save_settings()
        except Exception:
            pass

        # Restart the process
        if getattr(sys, "frozen", False):
            program = sys.argv[0]
            arguments = sys.argv[1:]
            workdir = os.path.dirname(program) or os.getcwd()
        else:
            program = sys.executable
            arguments = sys.argv
            workdir = os.getcwd()

        ok = QtCore.QProcess.startDetached(program, arguments, workdir)
        if not ok:
            QtWidgets.QMessageBox.critical(self, "Relaunch", "Failed to start a new process.")
            return
        QtWidgets.QApplication.quit()

    # ---------------- Persistence (QSettings) ----------------
    def load_settings(self):
        s = QtCore.QSettings()
        # Window geometry/state
        geom = s.value("window/geometry", None)
        state = s.value("window/state", None)
        if geom is not None:
            self.restoreGeometry(geom)
        if state is not None:
            self.restoreState(state)

        # Root directory
        root = s.value("app/rootDir", "", type=str)
        if not root:
            root = os.getcwd()
        self.root_edit.setText(root)

        # Patch editor content
        text = s.value("app/patchText", "", type=str)
        if text:
            self.patch_edit.setPlainText(text)

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
