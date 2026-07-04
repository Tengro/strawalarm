"""strawalarm GUI (PySide6). Native Qt widgets so it follows the system
theme (Breeze on KDE Plasma) in both light and dark variants."""

import datetime as dt
import sys
from importlib import resources

import shiboken6

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QRadioButton, QSlider, QSpinBox, QSystemTrayIcon,
    QToolButton, QVBoxLayout, QWidget)

from . import __version__, power
from .core import (SNOOZABLE, Phase, Session, SleepSpec, WakeSpec,
                   fmt_delta, next_occurrence, parse_duration,
                   parse_wake_time)
from .mpris import PROXY_PREFIXES, Player

TICK_MS = 250


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.session = None
        self._quitting = False
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self.on_tick)
        self.setWindowTitle("Strawalarm")
        self.setCentralWidget(self._build_ui())
        self._build_tray()
        self.refresh_players()

    def _build_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu(self)
        menu.addAction("Show/Hide").triggered.connect(self.toggle_window)
        self.tray_snooze = menu.addAction("Snooze")
        self.tray_snooze.setEnabled(False)
        self.tray_snooze.triggered.connect(self.do_snooze)
        self.tray_cancel = menu.addAction("Cancel timer")
        self.tray_cancel.setEnabled(False)
        self.tray_cancel.triggered.connect(self.cancel_from_tray)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.setToolTip("Strawalarm — idle")
        self.tray.show()

    def toggle_window(self):
        self.setVisible(not self.isVisible())
        if self.isVisible():
            self.raise_()
            self.activateWindow()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_window()

    def cancel_from_tray(self):
        if self.session and self.session.active:
            self.session.cancel()
            self.finish_session()

    def do_snooze(self):
        if self.session:
            self.session.snooze()

    def quit_app(self):
        if self.session and self.session.active:
            self.show()
            answer = QMessageBox.question(
                self, "Strawalarm",
                "A timer or alarm is armed. Quit and cancel it?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.session.cancel()
        self._quitting = True
        QApplication.quit()

    # ---------- UI construction ----------

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setSpacing(10)

        # player row
        row = QHBoxLayout()
        row.addWidget(QLabel("Player:"))
        self.player_combo = QComboBox()
        self.player_combo.currentIndexChanged.connect(self.refresh_playlists)
        row.addWidget(self.player_combo, 1)
        refresh = QToolButton()
        refresh.setIcon(QIcon.fromTheme("view-refresh"))
        refresh.setToolTip("Rescan running players and open playlists")
        refresh.clicked.connect(self.refresh_players)
        row.addWidget(refresh)
        layout.addLayout(row)

        # sleep group
        self.sleep_group = QGroupBox("Sleep timer — stop playback")
        self.sleep_group.setCheckable(True)
        form = QFormLayout(self.sleep_group)

        self.radio_duration = QRadioButton("After")
        self.duration_edit = QLineEdit("1h30m")
        self.duration_edit.setPlaceholderText("1h30m · 90m · 01:30:00")
        self.radio_duration.setChecked(True)
        form.addRow(self.radio_duration, self.duration_edit)

        self.radio_sleep_at = QRadioButton("At")
        self.sleep_at_edit = QLineEdit()
        self.sleep_at_edit.setPlaceholderText("23:45 or 23:45:30")
        form.addRow(self.radio_sleep_at, self.sleep_at_edit)

        self.radio_tracks = QRadioButton("After tracks")
        self.tracks_spin = QSpinBox()
        self.tracks_spin.setRange(1, 99)
        self.tracks_spin.setValue(3)
        self.tracks_spin.setSuffix(" track(s)")
        form.addRow(self.radio_tracks, self.tracks_spin)

        self.sleep_hint = QLabel()
        self.sleep_hint.setEnabled(False)  # muted, theme-correct grey
        form.addRow(self.sleep_hint)
        for w in (self.radio_duration, self.radio_sleep_at,
                  self.radio_tracks):
            w.toggled.connect(self._sync_sleep_mode)
        for w in (self.duration_edit, self.sleep_at_edit):
            w.textChanged.connect(self._update_sleep_hint)
        self.tracks_spin.valueChanged.connect(self._update_sleep_hint)

        self.fade_out_check = QCheckBox("Fade out over")
        self.fade_out_check.setChecked(True)
        self.fade_out_spin = QSpinBox()
        self.fade_out_spin.setRange(1, 600)
        self.fade_out_spin.setValue(30)
        self.fade_out_spin.setSuffix(" s")
        self.fade_out_check.toggled.connect(self.fade_out_spin.setEnabled)
        form.addRow(self.fade_out_check, self.fade_out_spin)

        self.pause_check = QCheckBox("Pause instead of stop (keeps position)")
        form.addRow(self.pause_check)

        self.suspend_check = QCheckBox("Suspend the PC after stopping")
        if not power.can_suspend():
            self.suspend_check.setEnabled(False)
            self.suspend_check.setToolTip("logind reports suspend "
                                          "is unavailable on this system")
        form.addRow(self.suspend_check)
        layout.addWidget(self.sleep_group)

        # wake group
        self.wake_group = QGroupBox("Alarm — wake up with music")
        self.wake_group.setCheckable(True)
        form = QFormLayout(self.wake_group)

        self.radio_wake_at = QRadioButton("At")
        self.wake_at_edit = QLineEdit("07:30")
        self.wake_at_edit.setPlaceholderText("07:30 or 07:30:00")
        self.radio_wake_at.setChecked(True)
        form.addRow(self.radio_wake_at, self.wake_at_edit)

        self.radio_wake_after = QRadioButton("After")
        self.wake_after_edit = QLineEdit("8h")
        self.wake_after_edit.setPlaceholderText("8h · 7h30m · 450")
        form.addRow(self.radio_wake_after, self.wake_after_edit)

        day_row = QHBoxLayout()
        day_row.setSpacing(2)
        self.day_buttons = []
        for name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"):
            b = QToolButton()
            b.setText(name)
            b.setCheckable(True)
            b.toggled.connect(self._update_wake_hint)
            day_row.addWidget(b)
            self.day_buttons.append(b)
        day_row.addStretch()
        repeat_label = QLabel("Repeat on:")
        repeat_label.setToolTip(
            "Pick days to make an alarm-only session recurring; "
            "no days = one-shot")
        form.addRow(repeat_label, day_row)

        self.wake_hint = QLabel()
        self.wake_hint.setEnabled(False)
        form.addRow(self.wake_hint)
        for w in (self.radio_wake_at, self.radio_wake_after):
            w.toggled.connect(self._sync_wake_mode)
        for w in (self.wake_at_edit, self.wake_after_edit):
            w.textChanged.connect(self._update_wake_hint)

        self.playlist_combo = QComboBox()
        self.playlist_combo.setToolTip(
            "Playlists currently open in the player (via MPRIS)")
        form.addRow("Playlist:", self.playlist_combo)

        self.volume_check = QCheckBox("Set volume to")
        self.volume_check.setChecked(True)
        vol_row = QHBoxLayout()
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(40)
        self.volume_label = QLabel("40%")
        self.volume_label.setMinimumWidth(40)
        self.volume_slider.valueChanged.connect(
            lambda v: self.volume_label.setText(f"{v}%"))
        self.volume_check.toggled.connect(self.volume_slider.setEnabled)
        vol_row.addWidget(self.volume_slider)
        vol_row.addWidget(self.volume_label)
        form.addRow(self.volume_check, vol_row)

        self.fade_in_check = QCheckBox("Fade in over")
        self.fade_in_check.setChecked(True)
        self.fade_in_spin = QSpinBox()
        self.fade_in_spin.setRange(1, 600)
        self.fade_in_spin.setValue(30)
        self.fade_in_spin.setSuffix(" s")
        self.fade_in_check.toggled.connect(self.fade_in_spin.setEnabled)
        form.addRow(self.fade_in_check, self.fade_in_spin)

        self.wake_system_check = QCheckBox("Wake the PC from suspend")
        self.wake_lead_spin = QSpinBox()
        self.wake_lead_spin.setRange(1, 30)
        self.wake_lead_spin.setValue(3)
        self.wake_lead_spin.setSuffix(" min early")
        if power.wake_backend():
            self.wake_system_check.setChecked(True)
        else:
            self.wake_system_check.setEnabled(False)
            self.wake_lead_spin.setEnabled(False)
            self.wake_system_check.setToolTip(
                "No wake-from-suspend backend found "
                "(needs KDE PowerDevil or rtcwake)")
        self.wake_system_check.toggled.connect(self.wake_lead_spin.setEnabled)
        form.addRow(self.wake_system_check, self.wake_lead_spin)

        self.keep_awake_spin = QSpinBox()
        self.keep_awake_spin.setRange(0, 240)
        self.keep_awake_spin.setValue(30)
        self.keep_awake_spin.setSuffix(" min")
        self.keep_awake_spin.setSpecialValueText("off")
        self.keep_awake_spin.setToolTip(
            "Block sleep for this long after the alarm fires")
        form.addRow("Keep awake after:", self.keep_awake_spin)

        self.snooze_spin = QSpinBox()
        self.snooze_spin.setRange(1, 60)
        self.snooze_spin.setValue(10)
        self.snooze_spin.setSuffix(" min")
        form.addRow("Snooze for:", self.snooze_spin)
        layout.addWidget(self.wake_group)

        # status
        self.phase_label = QLabel("Ready.")
        self.phase_label.setAlignment(Qt.AlignCenter)
        self.countdown_label = QLabel("0:00:00")
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.countdown_label.setEnabled(False)  # greyed out while idle
        font = QFont()
        font.setPointSize(30)
        font.setBold(True)
        self.countdown_label.setFont(font)
        layout.addWidget(self.phase_label)
        layout.addWidget(self.countdown_label)

        # start/cancel + snooze
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.clicked.connect(self.on_start_cancel)
        btn_row.addWidget(self.start_btn, 2)
        self.snooze_btn = QPushButton("Snooze")
        self.snooze_btn.setMinimumHeight(40)
        self.snooze_btn.setEnabled(False)
        self.snooze_btn.clicked.connect(self.do_snooze)
        btn_row.addWidget(self.snooze_btn, 1)
        layout.addLayout(btn_row)

        # log
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(96)
        layout.addWidget(self.log_view)

        self._sync_sleep_mode()
        self._sync_wake_mode()
        return root

    def _sync_sleep_mode(self):
        self.duration_edit.setEnabled(self.radio_duration.isChecked())
        self.sleep_at_edit.setEnabled(self.radio_sleep_at.isChecked())
        self.tracks_spin.setEnabled(self.radio_tracks.isChecked())
        self._update_sleep_hint()

    def _sync_wake_mode(self):
        at_mode = self.radio_wake_at.isChecked()
        self.wake_at_edit.setEnabled(at_mode)
        self.wake_after_edit.setEnabled(not at_mode)
        for b in self.day_buttons:  # repeating needs a fixed clock time
            b.setEnabled(at_mode)
        self._update_wake_hint()

    def _selected_days(self):
        days = tuple(i for i, b in enumerate(self.day_buttons)
                     if b.isChecked())
        return days if days and self.radio_wake_at.isChecked() else None

    @staticmethod
    def _fmt_target(t: dt.datetime) -> str:
        day = "today" if t.date() == dt.date.today() else "tomorrow"
        left = (t - dt.datetime.now()).total_seconds()
        return f"{day} at {t:%H:%M:%S} (in {fmt_delta(left)})"

    def _update_sleep_hint(self):
        try:
            if self.radio_tracks.isChecked():
                n = self.tracks_spin.value()
                hint = f"→ stops when track {n} ends (current track is #1)"
            elif self.radio_duration.isChecked():
                secs = parse_duration(self.duration_edit.text())
                t = dt.datetime.now() + dt.timedelta(seconds=secs)
                hint = f"→ stops {self._fmt_target(t)}"
            else:
                t = parse_wake_time(self.sleep_at_edit.text())
                hint = f"→ stops {self._fmt_target(t)}"
        except ValueError:
            hint = "→ enter a time like 23:45 or a duration like 1h30m"
        self.sleep_hint.setText(hint)

    def _update_wake_hint(self):
        try:
            days = self._selected_days()
            if self.radio_wake_at.isChecked():
                t = next_occurrence(self.wake_at_edit.text(), days)
            else:
                secs = parse_duration(self.wake_after_edit.text())
                if secs >= 24 * 3600:
                    raise ValueError("under 24h please")
                t = dt.datetime.now() + dt.timedelta(seconds=secs)
            when = f"{t:%a} " if days else ""
            left = (t - dt.datetime.now()).total_seconds()
            hint = (f"→ alarm {when}{t:%H:%M:%S} (in {fmt_delta(left)})"
                    + (" · repeats" if days else ""))
        except ValueError:
            hint = "→ enter a time like 07:30 or a duration like 8h"
        self.wake_hint.setText(hint)

    # ---------- data ----------

    def log(self, msg):
        self.log_view.appendPlainText(
            f"[{dt.datetime.now():%H:%M:%S}] {msg}")

    def refresh_players(self):
        current = self.player_combo.currentData()
        self.player_combo.blockSignals(True)
        self.player_combo.clear()
        for p in Player.list_all():
            self.player_combo.addItem(f"{p.identity()} ({p.name})", p.name)
        idx = self.player_combo.findData(current) if current else -1
        if idx < 0:  # default to the first real player, not a proxy
            for i in range(self.player_combo.count()):
                if not self.player_combo.itemData(i).startswith(PROXY_PREFIXES):
                    idx = i
                    break
        if idx >= 0:
            self.player_combo.setCurrentIndex(idx)
        self.player_combo.blockSignals(False)
        if self.player_combo.count() == 0:
            self.phase_label.setText("No MPRIS players running — "
                                     "start your music player and refresh.")
        self.refresh_playlists()

    def current_player(self):
        name = self.player_combo.currentData()
        return Player(name) if name else None

    def refresh_playlists(self):
        self.playlist_combo.clear()
        player = self.current_player()
        if player and player.has_playlists():
            self.playlist_combo.setEnabled(True)
            self.playlist_combo.addItem("(don't switch — just resume)", None)
            for _path, name in player.playlists():
                self.playlist_combo.addItem(name, name)
        else:
            self.playlist_combo.addItem("(player has no MPRIS playlists)",
                                        None)
            self.playlist_combo.setEnabled(False)

    # ---------- session control ----------

    def on_start_cancel(self):
        if self.session and self.session.active:
            self.session.cancel()
            self.finish_session()
            return
        player = self.current_player()
        if not player:
            QMessageBox.warning(self, "Strawalarm",
                                "No MPRIS player selected.")
            return
        if not self.sleep_group.isChecked() and not self.wake_group.isChecked():
            QMessageBox.warning(self, "Strawalarm",
                                "Enable the sleep timer, the alarm, or both.")
            return

        sleep = wake = None
        try:
            if self.sleep_group.isChecked():
                fade = self.fade_out_spin.value() \
                    if self.fade_out_check.isChecked() else 0
                sleep = SleepSpec(fade=fade,
                                  pause=self.pause_check.isChecked(),
                                  suspend_after=self.suspend_check.isChecked())
                if self.radio_tracks.isChecked():
                    sleep.tracks = self.tracks_spin.value()
                elif self.radio_duration.isChecked():
                    sleep.seconds = parse_duration(self.duration_edit.text())
                else:
                    t = parse_wake_time(self.sleep_at_edit.text())
                    sleep.seconds = max(
                        1, int((t - dt.datetime.now()).total_seconds()))
                if not sleep.tracks and sleep.seconds == 0:
                    raise ValueError("Sleep duration is zero.")
            if self.wake_group.isChecked():
                if self.radio_wake_at.isChecked():
                    time_spec = self.wake_at_edit.text().strip()
                    parse_wake_time(time_spec)  # validate now, arm later
                else:
                    secs = parse_duration(self.wake_after_edit.text())
                    if secs >= 24 * 3600:
                        raise ValueError("Wake delay must be under 24 hours.")
                    target = dt.datetime.now() + dt.timedelta(seconds=secs)
                    time_spec = target.strftime("%H:%M:%S")
        except ValueError as e:
            QMessageBox.warning(self, "Strawalarm", str(e))
            return
        if self.wake_group.isChecked():
            wake = WakeSpec(
                time_spec=time_spec,
                playlist=self.playlist_combo.currentData(),
                volume=self.volume_slider.value()
                if self.volume_check.isChecked() else None,
                fade=self.fade_in_spin.value()
                if self.fade_in_check.isChecked() else 0,
                wake_system=self.wake_system_check.isChecked(),
                wake_lead=self.wake_lead_spin.value() * 60,
                keep_awake=self.keep_awake_spin.value() * 60,
                snooze=self.snooze_spin.value() * 60,
                weekdays=self._selected_days())

        self.session = Session(player, sleep=sleep, wake=wake, log=self.log)
        try:
            self.session.start()
        except (RuntimeError, LookupError, ValueError) as e:
            QMessageBox.warning(self, "Strawalarm", str(e))
            self.session = None
            return
        self.set_running(True)
        self.timer.start()

    def on_tick(self):
        if not self.session:
            return
        self.session.tick()
        text, countdown = self.session.status()
        self.phase_label.setText(text)
        self.tray.setToolTip(
            f"Strawalarm — {text}"
            + (f" {fmt_delta(countdown)}" if countdown is not None else ""))
        self.countdown_label.setEnabled(True)
        self.countdown_label.setText(
            fmt_delta(countdown) if countdown is not None else "•")
        snoozable = self.session.phase in SNOOZABLE
        self.snooze_btn.setEnabled(snoozable)
        self.tray_snooze.setEnabled(snoozable)
        if not self.session.active:
            s = self.session
            if (s.phase == Phase.DONE and not s.cancelled and s.wake
                    and s.wake.weekdays and not s.sleep):
                self.log("Recurring alarm — re-arming for the next "
                         "scheduled day.")
                self.session = Session(s.player, wake=s.wake, log=self.log)
                try:
                    self.session.start()
                    return
                except (RuntimeError, LookupError, ValueError) as e:
                    self.log(f"Error re-arming: {e}")
            self.finish_session()

    def finish_session(self):
        self.timer.stop()
        if self.session and self.session.phase == Phase.ERROR:
            self.phase_label.setText(f"Error: {self.session.error}")
        elif self.session:
            self.phase_label.setText("Done.")
        self.countdown_label.setText("0:00:00")
        self.countdown_label.setEnabled(False)
        self.set_running(False)
        self.session = None
        self.tray.setToolTip("Strawalarm — idle")
        if not self.isVisible():
            self.tray.showMessage("Strawalarm", self.phase_label.text(),
                                  app_icon(), 5000)

    def set_running(self, running):
        for w in (self.player_combo, self.sleep_group, self.wake_group):
            w.setEnabled(not running)
        self.start_btn.setText("Cancel" if running else "Start")
        self.tray_cancel.setEnabled(running)
        if not running:
            self.snooze_btn.setEnabled(False)
            self.tray_snooze.setEnabled(False)

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
            return
        if self.session and self.session.active:
            if self.tray.isVisible():  # keep the armed timer alive in the tray
                self.hide()
                self.tray.showMessage(
                    "Strawalarm is still armed",
                    "Running in the system tray. Click the icon to reopen, "
                    "right-click to cancel or quit.", app_icon(), 5000)
                event.ignore()
                return
            answer = QMessageBox.question(
                self, "Strawalarm",
                "A timer or alarm is armed. Quit and cancel it?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.session.cancel()
        event.accept()
        QApplication.quit()


def app_icon():
    try:
        ref = resources.files("strawalarm").joinpath("data/strawalarm.svg")
        return QIcon(str(ref))
    except Exception:
        return QIcon.fromTheme("alarm-symbolic")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("strawalarm")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("strawalarm")
    app.setWindowIcon(app_icon())
    # The window can hide to the tray while a timer is armed, so quitting
    # is always explicit (closeEvent / quit_app call QApplication.quit()).
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    rc = app.exec()
    # Deterministic teardown: force-destroy the C++ objects while the
    # interpreter is fully alive. Letting PySide's atexit hook destroy
    # QApplication during Py_Finalize segfaults (Shiboken tears wrappers
    # down in arbitrary order with the Plasma platform theme loaded).
    # Plain `del app` is not enough — PySide holds an internal reference.
    win.hide()
    win.tray.hide()
    shiboken6.delete(win)
    shiboken6.delete(app)
    sys.exit(rc)


if __name__ == "__main__":
    main()
