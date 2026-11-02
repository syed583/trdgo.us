import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))
import smart_move_keep_awake as K  # noqa: E402


def test_requests_system_required_continuously_and_releases_only_its_own_request():
    calls = []
    with K.KeepAwake(api=lambda flags: calls.append(flags) or 1) as ka:
        assert ka.active and calls == [K.ES_CONTINUOUS | K.ES_SYSTEM_REQUIRED]
    assert calls[-1] == K.ES_CONTINUOUS and not ka.active                       # released on exit; no display flag, no power-plan change


def test_a_refused_request_and_a_non_windows_host_are_reported_not_hidden():
    ka = K.KeepAwake(api=lambda f: 0)
    assert ka.request() is False and not ka.active
    other = K.KeepAwake(platform="linux")
    assert other.supported is False and other.request() is False
    other.release()


def test_released_on_error():
    calls = []
    try:
        with K.KeepAwake(api=lambda f: calls.append(f) or 1):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert calls[-1] == K.ES_CONTINUOUS
