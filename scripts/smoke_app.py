"""Швидка перевірка, що додаток рендериться без помилок: python scripts/smoke_app.py"""
from streamlit.testing.v1 import AppTest

at = AppTest.from_file("../app.py", default_timeout=120).run()
assert not at.exception, at.exception
print("OK, вкладок:", len(at.tabs), "| метрики:", [m.value for m in at.metric])
