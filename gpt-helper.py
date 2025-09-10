import sys
import os
from PySide6 import QtWidgets, QtCore, QtGui


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Interactive Patch Helper (Skeleton)")
        self.resize(1000, 700)

        # Central layout
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

        top_row.addWidget(QtWidgets.QLabel("Root:"))
        top_row.addWidget(self.root_edit, stretch=1)
        top_row.addWidget(choose_btn)

        # Large text area for pasting patch text
        self.patch_edit = QtWidgets.QPlainTextEdit()
        self.patch_edit.setPlaceholderText("Paste patch text here…")

        # Set a monospace font (cross-platform)
        # Prefer using QFontDatabase to get the system fixed font
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.patch_edit.setFont(fixed_font)

        layout.addWidget(self.patch_edit, stretch=1)

        # Status bar
        self.statusBar().showMessage("Ready")

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
