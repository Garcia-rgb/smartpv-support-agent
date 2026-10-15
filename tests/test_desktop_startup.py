import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SCRIPT = Path(__file__).parents[1] / "scripts/start_desktop.py"


class Event:
    def __iadd__(self, callback):
        return self


def test_loading_window_precedes_service_initialization(tmp_path, monkeypatch):
    module = runpy.run_path(str(SCRIPT))
    scope = module["main"].__globals__
    window = SimpleNamespace(events=SimpleNamespace(loaded=Event()), load_url=Mock(),
                             restore=Mock(), show=Mock())
    create = Mock(return_value=window)
    checked = []

    def ready(url, instance):
        assert create.called
        checked.append(url)
        from support_agent import __version__
        return __version__

    def start(initialize, **kwargs):
        assert create.called and not checked
        assert "正在加载" in create.call_args.kwargs["html"]
        initialize()

    def launch(args, **kwargs):
        assert create.called
        token = args[args.index("--token") + 1]
        (tmp_path / ".desktop/service-ready.json").write_text(json.dumps({
            "token": token, "pid": 17, "instance": "test-instance",
        }))
        return SimpleNamespace(pid=17, poll=lambda: None, wait=Mock(), terminate=Mock())

    monkeypatch.setitem(scope, "DATA_ROOT", tmp_path)
    monkeypatch.setitem(scope, "ready_version", ready)
    monkeypatch.setattr(scope["subprocess"], "Popen", launch)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(
        settings={}, create_window=create, start=start,
    ))
    monkeypatch.setitem(sys.modules, "start_client", SimpleNamespace(
        HOST="127.0.0.1", pick_port=lambda: 8000, reachable=lambda *a: False,
    ))
    monkeypatch.chdir(tmp_path)
    assert module["main"]() == 0
    window.restore.assert_called_once()
    window.show.assert_called_once()
    window.load_url.assert_called_once_with("http://127.0.0.1:8000")


def test_activation_requests_are_distinct_and_restore_before_show(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    module["request_activation"](tmp_path)
    first = (tmp_path / ".desktop/activate.request").read_text()
    module["request_activation"](tmp_path)
    assert (tmp_path / ".desktop/activate.request").read_text() != first
    window = Mock()
    module["activate_window"](window)
    assert [call[0] for call in window.mock_calls] == ["restore", "show"]
