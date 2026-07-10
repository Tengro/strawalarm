"""Preview: a ~15s compressed alarm dry-run that restores the player."""

from strawalarm.core import PREVIEW_FADE, PREVIEW_PLAY, Preview


def drive(preview, clock, step=0.1):
    while preview.active:
        clock.advance(step)
        preview.tick()


class TestPreview:
    def test_full_cycle_restores_everything(self, clock, player, fpower):
        player._status = "Paused"
        player._volume = 0.7
        p = Preview(player, playlist="Morning", volume=40)
        p.start()
        assert player.called("activate")[0] == ("activate", "/pl/1")
        drive(p, clock)
        # restored: previous playlist, paused status, original volume
        assert player.called("activate")[-1] == ("activate", "/pl/previous")
        assert player.status() == "Paused"
        assert player.volume() == 0.7

    def test_fade_reaches_target_volume(self, clock, player, fpower):
        p = Preview(player, volume=80)
        p.start()
        clock.advance(PREVIEW_FADE + 0.2)
        p.tick()
        assert abs(player.volume() - 0.8) < 0.01
        drive(p, clock)

    def test_duration_is_compressed(self, clock, player, fpower):
        p = Preview(player)
        start = clock.t
        p.start()
        drive(p, clock)
        assert clock.t - start <= PREVIEW_FADE + PREVIEW_PLAY + 1

    def test_early_finish_restores(self, clock, player, fpower):
        player._status = "Stopped"
        p = Preview(player, playlist="Morning")
        p.start()
        clock.advance(1)
        p.tick()
        p.finish()  # user clicked "Stop preview"
        assert not p.active
        assert player.status() == "Stopped"
        assert player.volume() == 0.5

    def test_not_running_player_refuses(self, clock, player, fpower):
        import pytest
        player._running = False
        with pytest.raises(RuntimeError):
            Preview(player).start()

    def test_failed_playlist_lookup_leaves_player_untouched(self, clock,
                                                            player, fpower):
        """Regression: a bad playlist name must not leave volume at 0
        (found by a live test against Strawberry)."""
        import pytest
        with pytest.raises(LookupError):
            Preview(player, playlist="Nonexistent").start()
        assert player.volume() == 0.5  # never muted
        assert not player.called("set_volume")
