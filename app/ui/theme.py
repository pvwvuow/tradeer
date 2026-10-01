"""Small token-based theme for the Phase 1 shell."""

DARK_QSS = """
QWidget { background: #0B0D12; color: #E6E8EE; font-family: 'Segoe UI'; }
QFrame#sidebar, QFrame#card { background: #12151C; border: 1px solid #232836; border-radius: 12px; }
QLabel#muted { color: #8A91A5; }
QLabel#title { font-size: 24px; font-weight: 700; }
QPushButton { background: #171B24; border: 1px solid #2d3445; border-radius: 8px; padding: 9px 12px; }
QPushButton:hover { border-color: #5B8CFF; }
QPushButton#accent { background: #5B8CFF; color: white; border: none; }
"""

LIGHT_QSS = DARK_QSS.replace("#0B0D12", "#F7F8FA").replace("#12151C", "#FFFFFF").replace("#171B24", "#F0F2F5").replace("#E6E8EE", "#18202B").replace("#8A91A5", "#5D6675")
