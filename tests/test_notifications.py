"""
tests/test_notifications.py — Mixtape

Regression tests for notification creation.

These target Issue #4: rating a song must notify the song's original sharer,
mirroring the existing "song added to playlist" notification. The
test_rating_a_song_notifies_the_sharer case fails against the buggy code
(rate_song created no notification) and passes after the fix.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed(app):
    """A sharer who shared a song, plus a separate rater."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="After Hours", artist="Night City", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_a_song_notifies_the_sharer(app, seed):
    """
    When a user rates someone else's song, the original sharer receives a
    'song_rated' notification. This would have caught Issue #4: before the fix,
    rate_song saved the rating but created no notification, so this count was 0.
    """
    with app.app_context():
        sharer_id = seed["sharer"].id
        rater_id = seed["rater"].id
        song_id = seed["song"].id

        rate_song(rater_id, song_id, 5)

        notifs = get_notifications(sharer_id)
        rated_notifs = [n for n in notifs if n["type"] == "song_rated"]
        assert len(rated_notifs) == 1


def test_rating_your_own_song_does_not_notify(app, seed):
    """
    A user rating their own shared song should not generate a self-notification.
    """
    with app.app_context():
        sharer_id = seed["sharer"].id
        song_id = seed["song"].id

        rate_song(sharer_id, song_id, 4)

        notifs = get_notifications(sharer_id)
        assert len(notifs) == 0
