"""
tests/test_playlists.py — Mixtape

Tests for playlist retrieval logic.
"""

import pytest
from app import create_app, db
from models import User, Song, Playlist, playlist_entries
from services.playlist_service import create_playlist, get_playlist_songs


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed_playlist(app):
    """Create a playlist with 5 songs for testing."""
    with app.app_context():
        user = User(username="dj", email="dj@example.com")
        db.session.add(user)
        db.session.flush()

        songs = [
            Song(title=f"Track {i}", artist="Various", shared_by=user.id)
            for i in range(1, 6)
        ]
        db.session.add_all(songs)
        db.session.flush()

        playlist = Playlist(name="My Playlist", created_by=user.id)
        db.session.add(playlist)
        db.session.flush()

        for i, song in enumerate(songs):
            db.session.execute(
                playlist_entries.insert().values(
                    playlist_id=playlist.id,
                    song_id=song.id,
                    position=i + 1,
                    added_by=user.id,
                )
            )

        db.session.commit()
        yield {"user": user, "songs": songs, "playlist": playlist}


def test_playlist_returns_all_songs(app, seed_playlist):
    """
    get_playlist_songs should return all songs in the playlist.
    """
    with app.app_context():
        playlist_id = seed_playlist["playlist"].id
        songs = get_playlist_songs(playlist_id)
        assert len(songs) == 5  # Bug causes this to return 4


def test_playlist_returns_songs_in_order(app, seed_playlist):
    """Songs should be returned in position order."""
    with app.app_context():
        playlist_id = seed_playlist["playlist"].id
        songs = get_playlist_songs(playlist_id)
        titles = [s["title"] for s in songs]
        assert titles == ["Track 1", "Track 2", "Track 3", "Track 4", "Track 5"]


def test_empty_playlist_returns_empty_list(app):
    """An empty playlist should return an empty list without error."""
    with app.app_context():
        user = User(username="newdj", email="newdj@example.com")
        db.session.add(user)
        db.session.flush()

        playlist = Playlist(name="Empty Playlist", created_by=user.id)
        db.session.add(playlist)
        db.session.commit()

        songs = get_playlist_songs(playlist.id)
        assert songs == []


def test_playlist_newest_song_and_single_song_are_visible(app):
    """Read back one entry, then append a second and retain both in order."""
    with app.app_context():
        user = User(username='boundary', email='boundary@example.com')
        db.session.add(user)
        db.session.flush()
        playlist = create_playlist('Boundary', user.id)
        songs = [Song(title=title, artist='Artist', shared_by=user.id)
                 for title in ['First', 'Second']]
        db.session.add_all(songs)
        db.session.flush()
        client = app.test_client()
        for position, song in enumerate(songs, start=1):
            db.session.execute(playlist_entries.insert().values(
                playlist_id=playlist.id, song_id=song.id,
                position=position, added_by=user.id))
            db.session.commit()
            result = client.get(f'/playlists/{playlist.id}/songs').json
            assert result['count'] == position
            assert [row['id'] for row in result['songs']] == [s.id for s in songs[:position]]
        assert client.get(f'/playlists/{playlist.id}').json['name'] == 'Boundary'


def test_add_song_http_records_position_contributor_and_notifies(app):
    """Exercise the reported add/refetch sequence rather than bypassing POST."""
    with app.app_context():
        sharer = User(username='owner', email='owner@example.com')
        adder = User(username='adder', email='adder@example.com')
        db.session.add_all([sharer, adder])
        db.session.flush()
        playlist = create_playlist('Friday Energy', adder.id)
        songs = [Song(title=f'Addition {i}', artist='Artist', shared_by=sharer.id)
                 for i in range(2)]
        db.session.add_all(songs)
        db.session.commit()
        client = app.test_client()
        for position, song in enumerate(songs, start=1):
            response = client.post(f'/playlists/{playlist.id}/songs',
                json={'song_id': song.id, 'added_by': adder.id})
            assert response.status_code == 201
            result = client.get(f'/playlists/{playlist.id}/songs').json
            assert [row['id'] for row in result['songs']] == [s.id for s in songs[:position]]
            entry = db.session.execute(playlist_entries.select().where(
                playlist_entries.c.playlist_id == playlist.id,
                playlist_entries.c.song_id == song.id)).one()
            assert entry.position == position
            assert entry.added_by == adder.id
        assert client.get(f'/users/{sharer.id}/notifications').json['count'] == 2
        assert client.post(f'/playlists/{playlist.id}/songs',
            json={'song_id': songs[0].id, 'added_by': adder.id}).status_code == 201
        assert client.get(f'/playlists/{playlist.id}/songs').json['count'] == 2
