import logging

from strawalarm import filelog


def fresh_logger():
    logger = logging.getLogger("strawalarm")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    return logger


def test_writes_to_xdg_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    fresh_logger()
    filelog.get_logger().info("hello %s", "world")
    logfile = tmp_path / "strawalarm" / "strawalarm.log"
    assert logfile.exists()
    assert "hello world" in logfile.read_text()


def test_reuses_handler(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    fresh_logger()
    first = filelog.get_logger()
    assert filelog.get_logger() is first
    assert len(first.handlers) == 1


def test_unwritable_state_dir_degrades_quietly(tmp_path, monkeypatch):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where a directory must go")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker / "sub"))
    fresh_logger()
    filelog.get_logger().info("no crash")  # NullHandler fallback
