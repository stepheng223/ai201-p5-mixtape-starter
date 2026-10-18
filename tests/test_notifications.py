"""Rating notification regression through HTTP and related notification paths."""
from app import create_app, db
from models import User, Song, Playlist, Rating, playlist_entries
from services.notification_service import add_to_playlist


def test_rating_notifies_sharer_and_preserves_rating_and_notification_behaviors():
    app = create_app({'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
    with app.app_context():
        sharer = User(username='sharer', email='sharer@example.com')
        rater = User(username='rater', email='rater@example.com')
        db.session.add_all([sharer, rater])
        db.session.flush()
        song = Song(title='Anthem', artist='Artist', shared_by=sharer.id)
        playlist = Playlist(name='Energy', created_by=rater.id)
        db.session.add_all([song, playlist])
        db.session.commit()
        client = app.test_client()
        url = f'/songs/{song.id}/rate'
        response = client.post(url, json={'user_id': rater.id, 'score': 5})
        assert response.status_code == 201
        notifications = client.get(f'/users/{sharer.id}/notifications').json['notifications']
        assert len(notifications) == 1
        assert notifications[0]['type'] == 'song_rated'
        assert 'rater' in notifications[0]['body'] and 'Anthem' in notifications[0]['body']
        assert '5' in notifications[0]['body']
        assert client.post(url, json={'user_id': rater.id, 'score': 3}).status_code == 201
        assert Rating.query.filter_by(user_id=rater.id, song_id=song.id).count() == 1
        assert Rating.query.filter_by(user_id=rater.id, song_id=song.id).one().score == 3
        assert client.post(url, json={'user_id': sharer.id, 'score': 4}).status_code == 201
        assert client.post(url, json={'user_id': rater.id, 'score': 6}).status_code == 400
        assert client.get(f'/users/{sharer.id}/notifications').json['count'] == 2
        # Existing membership bypasses the separate association-insert defect.
        db.session.execute(playlist_entries.insert().values(
            playlist_id=playlist.id, song_id=song.id, position=1, added_by=rater.id))
        db.session.commit()
        add_to_playlist(playlist.id, song.id, rater.id)
        notifications = client.get(f'/users/{sharer.id}/notifications').json['notifications']
        assert sorted(n['type'] for n in notifications) == ['song_added_to_playlist', 'song_rated', 'song_rated']
        for notification in notifications:
            assert client.post(f"/users/notifications/{notification['id']}/read").status_code == 200
        assert client.get(f'/users/{sharer.id}/notifications?unread_only=true').json['count'] == 0
