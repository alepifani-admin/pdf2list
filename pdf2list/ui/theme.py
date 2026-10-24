"""Visual theme for the desktop app."""

from __future__ import annotations

QSS = """
* {
    font-family: "SF Pro Text", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}
QMainWindow, QDialog {
    background: #F6F7F9;
}
QLabel {
    color: #2E3033;
}
QWidget#Header {
    background: #2E3033;
    border-radius: 14px;
}
QLabel#AppTitle {
    color: #FFFFFF;
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QLabel#AppSubtitle {
    color: #C8CDD1;
    font-size: 12px;
}
QLabel#SectionLabel {
    color: #5A6066;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.6px;
}
QLabel#Muted {
    color: #7A8087;
    font-size: 12px;
}
QFrame#Card, QGroupBox {
    background: #FFFFFF;
    border: 1px solid #E3E6E9;
    border-radius: 12px;
}
QGroupBox {
    margin-top: 16px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
    color: #2E3033;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: #5A6066;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
QFrame#DropZone {
    background: #FFFFFF;
    border: 2px dashed #C8CDD1;
    border-radius: 14px;
}
QFrame#DropZone[dragActive="true"] {
    border: 2px solid #00A896;
    background: #F0FBF9;
}
QLabel#DropTitle {
    color: #2E3033;
    font-size: 15px;
    font-weight: 600;
}
QLabel#DropHint {
    color: #8A9097;
    font-size: 12px;
}
QPushButton {
    background: #FFFFFF;
    color: #2E3033;
    border: 1px solid #D7DBDF;
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background: #F1F3F5;
    border-color: #BFC4C8;
}
QPushButton:pressed {
    background: #E6E9EC;
}
QPushButton:disabled {
    color: #A9AFB5;
    background: #F4F5F6;
    border-color: #E3E6E9;
}
QPushButton#Primary {
    background: #2E3033;
    color: #FFFFFF;
    border: 1px solid #2E3033;
    font-weight: 600;
    padding: 9px 18px;
}
QPushButton#Primary:hover {
    background: #414448;
}
QPushButton#Accent {
    background: #00A896;
    color: #FFFFFF;
    border: 1px solid #00A896;
    font-weight: 600;
}
QPushButton#Accent:hover {
    background: #009383;
}
QPushButton#Ghost {
    background: transparent;
    border: 1px solid transparent;
    color: #5A6066;
    padding: 5px 8px;
}
QPushButton#Ghost:hover {
    background: #ECEEF0;
    border-radius: 7px;
}
QFrame#Header QPushButton#Ghost {
    color: #E6E9EC;
}
QFrame#Header QPushButton#Ghost:hover {
    background: #414448;
    color: #FFFFFF;
}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #FFFFFF;
    color: #2E3033;
    border: 1px solid #D7DBDF;
    border-radius: 8px;
    padding: 6px 9px;
    min-height: 20px;
    selection-background-color: #00A896;
    selection-color: #FFFFFF;
}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {
    border-color: #BFC4C8;
}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {
    border: 1px solid #00A896;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background: #FFFFFF;
    color: #2E3033;
    border: 1px solid #D7DBDF;
    border-radius: 8px;
    selection-background-color: #E8F7F4;
    selection-color: #2E3033;
    padding: 4px;
}
QCheckBox {
    spacing: 8px;
    color: #3A3F44;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border: 1px solid #C8CDD1;
    border-radius: 4px;
    background: #FFFFFF;
}
QCheckBox::indicator:checked {
    background: #00A896;
    border-color: #00A896;
}
QTabWidget::pane {
    border: 1px solid #E3E6E9;
    border-radius: 12px;
    background: #FFFFFF;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: #6E7377;
    padding: 8px 14px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 500;
}
QTabBar::tab:selected {
    background: #FFFFFF;
    color: #2E3033;
    border-color: #E3E6E9;
    border-bottom: 2px solid #00A896;
}
QTabBar::tab:hover:!selected {
    color: #2E3033;
    background: #F1F3F5;
}
QTableWidget {
    background: #FFFFFF;
    color: #232629;
    border: none;
    gridline-color: #EDEFF1;
    selection-background-color: #CDEFE8;
    selection-color: #16332E;
    alternate-background-color: #FAFBFC;
}
QTableWidget::item {
    color: #232629;
    padding: 4px 6px;
}
QTableWidget::item:selected {
    background: #CDEFE8;
    color: #16332E;
}
QHeaderView::section {
    background: #2E3033;
    color: #FFFFFF;
    padding: 7px 6px;
    border: none;
    border-right: 1px solid #414448;
    font-weight: 600;
    font-size: 12px;
}
QPlainTextEdit#Log {
    background: #1F2124;
    color: #D8DDE1;
    border: 1px solid #2E3033;
    border-radius: 10px;
    font-family: "SF Mono", "Consolas", "Menlo", monospace;
    font-size: 11px;
    padding: 8px;
}
QProgressBar {
    background: #E3E6E9;
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background: #00A896;
    border-radius: 4px;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #C8CDD1;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #A9AFB5;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #C8CDD1;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QMenu {
    background: #FFFFFF;
    border: 1px solid #D7DBDF;
    border-radius: 8px;
    padding: 6px;
}
QMenu::item {
    padding: 6px 24px 6px 12px;
    border-radius: 5px;
    color: #2E3033;
}
QMenu::item:disabled {
    color: #A9AFB5;
}
QMenu::item:selected {
    background: #E8F7F4;
    color: #2E3033;
}
QStatusBar {
    background: transparent;
    color: #6E7377;
}
QLabel#Badge {
    background: #E8F7F4;
    color: #007D70;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#Warning {
    background: #FFF6E5;
    color: #8A5A00;
    border: 1px solid #F3DFB0;
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 12px;
}
QToolTip {
    background: #2E3033;
    color: #FFFFFF;
    border: none;
    padding: 5px 8px;
    border-radius: 5px;
}
"""
