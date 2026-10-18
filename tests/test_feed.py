"""Calendar-day feed regression and related historical-feed checks."""
from datetime import datetime, timezone
from unittest.mock import patch

from app import create_app, db
from models import User, Song, ListeningEvent


def test_listening_now_uses_utc_midnight_and_keeps_latest_friend_event():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
    with app.app_context():
        viewer = User(username='viewer', email='viewer@example.com')
        friend = User(username='friend', email='friend@example.com')
        stranger = User(username='stranger', email='stranger@example.com')
        db.session.add_all([viewer, friend, stranger])
        db.session.flush()
        viewer.friends.append(friend)
        song = Song(title='Today', artist='Artist', shared_by=friend.id)
        db.session.add(song)
        db.session.flush()
        def event(user, day, hour, minute=0, second=0):
            return ListeningEvent(user_id=user.id, song_id=song.id,
                listened_at=datetime(2024, 6, day, hour, minute, second))
        old = event(friend, 15, 23, 59, 59)
        db.session.add(old)
        db.session.commit()
        client = app.test_client()
        with patch('services.feed_service.datetime') as clock:
            clock.now.return_value = datetime(2024, 6, 16, 9, tzinfo=timezone.utc)
            assert client.get(f'/feed/{viewer.id}/listening-now').json['count'] == 0
            assert client.get(f'/feed/{viewer.id}/activity').json['count'] == 1
            midnight = event(friend, 16, 0)
            db.session.add(midnight)
            db.session.commit()
            assert client.get(f'/feed/{viewer.id}/listening-now').json['count'] == 1
            db.session.add_all([event(friend, 16, 8), event(stranger, 16, 8)])
            db.session.commit()
            feed = client.get(f'/feed/{viewer.id}/listening-now').json['feed']
            assert len(feed) == 1
            assert feed[0]['friend']['id'] == friend.id
            assert feed[0]['listened_at'] == '2024-06-16T08:00:00'
            assert client.get(f'/feed/{viewer.id}/activity').json['count'] == 3
