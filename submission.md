# AI usage

This work was completed with Codex as an active coding agent. It read the assignment and repository, traced routes into services and models, implemented changes, and ran checks. These are agent-run checks, not claims that the student manually performed them. Student-performed code verification has not been recorded; agent-run checks below should not be read as student-run checks.

Specific uses included summarizing the Flask/SQLAlchemy architecture and tracing `POST /songs/<id>/rate` into `rate_song()`, then comparing its persistence and notification steps with `add_to_playlist()`. Codex also traced search results through `select(Song)`, the association-table join, and scalar result serialization to explain how tag cardinality affects result count. Diagnoses were checked against executable reproductions rather than accepted from explanation alone; detailed results follow below.

During resubmission review, the student supplied the actual grading rubric and full project brief rather than relying on Codex's initial speculation about the incomplete label. This corrected the review: text commit history is accepted for the required commit-history points, while the full brief still requests a screenshot as a deliverable. Codex then verified that the GitHub branch exists and reran all 18 tests successfully. This is a documented correction of the AI-assisted review, not a claim that the student independently verified application behavior.

# Codebase map

Orientation notes recorded before modifying application code:

- `app.py` owns the shared SQLAlchemy instance and `create_app(config)`. The factory applies configuration, initializes the database, registers four blueprints, and creates tables.
- `models.py` defines User, Song, Tag, ListeningEvent, Rating, Playlist, and Notification. Friendships and song tags use association tables. `playlist_entries` stores song positions, the contributor, and addition time. Model `to_dict()` methods produce API payloads.
- `routes/songs.py` exposes search, song detail, listening, and rating endpoints. `routes/playlists.py` exposes creation, metadata, ordered songs, and additions. `routes/feed.py` exposes listening-now and activity. `routes/users.py` exposes profiles, streaks, notifications, and marking notifications read.
- `services/streak_service.py` records listening events and maintains a stored streak using calendar dates. `services/feed_service.py` queries friends' events and builds listening-now and historical activity responses.
- `services/search_service.py` searches title/artist and retrieves individual songs. `services/playlist_service.py` creates playlists and retrieves metadata and position-ordered songs.
- `services/notification_service.py` saves ratings, adds songs to playlists, creates notifications, retrieves them, and marks them read.
- `seed_data.py` recreates sample users, friendships, songs, tags, events, playlists, and notifications. `tests/` initially contains isolated SQLite tests for streaks, searches, and playlist retrieval. `requirements.txt` lists runtime and test dependencies.

Data flow — rating: `POST /songs/<song_id>/rate` parses `user_id` and `score` in `routes/songs.py`, converts the score to an integer, and calls `notification_service.rate_song()`. The service validates the range and song/user existence, queries that user's rating for the song, creates or updates the Rating, and commits. The route serializes the returned model with `Rating.to_dict()` and returns JSON with status 201.

Data flow — listening-now: `GET /feed/<user_id>/listening-now` calls `get_friends_listening_now()`, loads the user's friends, filters their ListeningEvent rows by time, sorts newest first, and retains the newest event per friend. User and Song serializers supply the nested JSON. The route adds the count.

Pattern: routes generally parse requests and translate service results/errors into HTTP responses; services own queries and business operations. Profiles are a small exception: the user route queries User directly. Database timestamps use UTC defaults and SQLite may return them without timezone information.

# Root cause analyses

**Issue #1 — My listening streak keeps resetting**

- **How reproduced:** Before edits, ran the existing `test_streak_increments_on_sunday`: listening at noon UTC on June 15 and June 16, 2024 produced 1 instead of 2. The baseline suite had four failures and nine passes.
- **Navigation:** Followed `routes/songs.py`'s listen handler to `record_listening_event()`, then `update_listening_streak()` in `services/streak_service.py`; compared the date calculation with the stored User fields in `models.py`. The failing controlled Saturday/Sunday test and the explicit Sunday exclusion established the cause.
- **Root cause:** The consecutive-day branch required both `days_since_last == 1` and `today.weekday() != 6`. Python numbers Sunday as 6, so a Sunday listen fell through to the reset branch even when the previous listen was Saturday. Streak continuity depends on elapsed calendar days, not day of week.
- **Fix:** Removed the weekday restriction; a one-day date difference always increments the streak.
- **Side-effect check:** Ran all five streak tests. New users still start at 1, Monday-to-Tuesday increments, repeated same-day listens do not increment, and skipped days still reset. These exercise the neighboring branches of the modified condition.

**Issue #2 — Friends Listening Now shows people from yesterday**

- **How reproduced:** Added `tests/test_feed.py` before changing the service. With the clock fixed at June 16, 2024, 09:00 UTC and a friend's event at June 15, 23:59:59, the HTTP listening-now endpoint returned count 1 instead of 0.
- **Navigation:** Traced `routes/feed.py`'s listening-now handler into `get_friends_listening_now()` in `services/feed_service.py`, followed its friend-ID filter and ListeningEvent timestamp query, then compared the independent `get_activity_feed()` query. The fixed-clock failure confirmed the recency cutoff was the specific cause, rather than friendship or serialization.
- **Root cause:** `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD` used a rolling 24-hour window. At 09:00 this admits events since 09:00 yesterday, so last night's listening remains until its timestamp ages out. A calendar-day feed must use midnight instead of a duration before the current time.
- **Fix:** Compute today's midnight UTC and retain the inclusive `>=` comparison. UTC follows the app's timestamp convention; this app has no per-user timezone setting.
- **Side-effect check:** The new test verifies exclusion one second before midnight, inclusion at exactly midnight, newest-only deduplication after another event, and exclusion of a nonfriend. It also confirms the historical activity endpoint retains all three friend events including yesterday's. This checks the neighboring feed whose historical behavior must remain independent.

**Issue #3 — The same song keeps showing up twice in search**

- **How reproduced:** The unmodified multi-tag search test returned three copies of Crown Heights Anthem for a title match. Zero-tag and single-tag controls passed, isolating the conditional behavior to tag cardinality.
- **Navigation:** Followed `/songs/search` in `routes/songs.py` into `search_songs()` in `services/search_service.py`, then inspected `song_tags` and the `Song.tags` relationship in `models.py`. The SQL outer join emits one row per association, and `.scalars().all()` preserves those repeated rows; seeing three tags and exactly three results confirmed this mechanism.
- **Root cause:** The search joins `song_tags` even though its predicate only uses title and artist. A three-tag song therefore produces three scalar results referencing the same song, and the list comprehension serializes each occurrence. Songs with zero tags survive the outer join once; one tag produces one row. SQLAlchemy's identity map reuses model objects but does not remove repeated entries from this scalar result list.
- **Fix:** Removed the unnecessary association-table join. Selecting matching Song rows yields one result per primary key, while the existing `Song.tags` relationship continues loading every tag for serialization.
- **Side-effect check:** All six search tests pass: title/artist matches, zero/one/multiple tags, no match, and a new HTTP test asserting one result with all three tags for a lowercase artist query. The latter guards against accidentally fixing duplication by dropping associated tag data.

**Issue #4 — Rating notifications never arrive**

- **How reproduced:** Before the fix, the new HTTP regression in `tests/test_notifications.py` posted a score of 5 for another user's song. The rating endpoint returned 201, but fetching the sharer's notifications returned an empty list; the assertion expecting one notification failed.
- **Navigation:** Started at `routes/songs.py`'s rating endpoint and followed `rate_song()` in `services/notification_service.py`. Compared it with `routes/playlists.py` → `add_to_playlist()` → `create_notification()`, then inspected Notification persistence and the retrieval route. The working path explicitly creates a notification after the interaction; the rating path committed only Rating and returned. The saved rating plus empty notification query confirmed this missing step.
- **Root cause:** Saving a Rating does not automatically generate a Notification: there is no model hook or event listener connecting them. `rate_song()` never called `create_notification()`, so retrieval correctly found no row. This requires explicitly connecting the rating interaction to the notification service, rather than changing retrieval or adding a delay.
- **Fix:** After saving a valid rating, call `create_notification()` with type `song_rated`, the original sharer's ID, and a message naming the rater, song, and score. Like playlist notifications, self-interactions do not notify. Both creation and subsequent score updates notify because both are successful rating actions.
- **Side-effect check:** The regression confirms rating updates reuse one Rating row and persist the new score; self-ratings and rejected out-of-range scores add no notifications. It also exercises the existing playlist-add notification for an already-associated song and marks all notifications read, confirming unread filtering returns zero. Existing membership was used because inspection identified a separate new-membership insert problem involving required position/contributor fields; that follow-up is recorded separately below.

**Issue #5 — The last song in a playlist never shows up**

- **How reproduced:** On the original code, the five-song fixture returned four songs and failed both count and order tests. Before changing this service, a new single-entry HTTP regression returned count 0 instead of 1. Association rows were inserted explicitly to isolate retrieval from the independent POST insertion defect.
- **Navigation:** Followed `routes/playlists.py`'s GET handler to `get_playlist_songs()` in `services/playlist_service.py`; checked the `playlist_entries.position` model column and ordered join. The query retrieves all rows in ascending position, but the final serialization slices `songs[:-1]`. The missing last position in both failing tests confirmed that the loss occurs after querying.
- **Root cause:** Python's `[:-1]` slice excludes the final element. Since the query sorts by ascending position, it always drops the highest-position song. Adding a new final entry moves the previously hidden song out of that excluded slot. An empty list stays empty and a one-song playlist becomes empty, explaining the boundary cases.
- **Fix:** Serialize the entire ordered `songs` list. There is no sentinel row to remove; every row is a real playlist member.
- **Side-effect check:** All four playlist tests pass: empty lists, five-song count, exact position order, and a new single-song/append test that fetches JSON after each insertion and checks metadata remains available. This verifies both small boundaries and that retaining the final row preserves the prior order.

# Additional integration fix — playlist additions

The full Issue #5 POST/refetch check exposed a separate starter defect. A new HTTP test failed with `NOT NULL constraint failed: playlist_entries.position`. Tracing `add_to_playlist()` into the `Playlist.songs` relationship showed that plain relationship append supplies the two IDs but cannot supply required `position` and `added_by` association fields. Replaced append with an explicit association insert carrying the contributor and the next position (maximum existing position plus one, or 1 for an empty playlist), then expired the loaded relationship. The integration regression adds two songs through POST, fetches both in order, checks stored contributor/position, verifies two sharer notifications, and confirms readding a member does not duplicate membership. This is a separate supporting commit rather than being bundled with any of the five reported fixes. Position allocation follows this app's sequential request model; concurrent additions would need stronger transaction/ordering coordination.

# Regression evidence

Run `.venv/bin/python -m pytest tests/ -q` from the repository root. New tests include `tests/test_feed.py` (UTC midnight vs. yesterday and independent activity history), `tests/test_notifications.py` (persisted rating notifications and neighboring behaviors), and additional search/playlist tests. The feed and notification regressions were each run before the corresponding fix and failed with counts 1 instead of 0 and 0 instead of 1, respectively. The new single-song playlist regression failed with count 0 instead of 1 before removing the slice. These assertions would detect reintroduction of those bugs. Existing starter regressions also failed before the streak, search, and playlist fixes.

# Submission

Branch: `bugfix/mixtape`. Submission URL: https://github.com/stepheng223/ai201-p5-mixtape-starter/tree/bugfix/mixtape . The branch was confirmed published on GitHub during the October 17, 2026 resubmission review. Capture `git log --oneline` while on that branch for the required screenshot; text history is also recorded in `commit-history.txt`. The orientation map has its own commit before all application fixes. Each reported issue has its own subsequent `fix:` commit and RCA above.

Final validation: 18 tests passed; `git diff --check` passed. `seed_data.py` created 5 users, 13 songs, and 3 playlists. A seeded-app Flask test-client request to `/songs/search?q=Anthem` returned HTTP 200 and exactly one result. The real loopback Flask server also completed the curl request with HTTP 200 and exactly one Anthem result containing all three tags; its response arrived after the initial wait. The server was then stopped.


# Resubmission verification — October 17, 2026

Codex reran `.venv/bin/python -m pytest tests/ -q`: **18 passed**. `git diff --check` also passed. The five independent issue commits are preserved; no fixes were squashed. The architectural map was originally committed in `4ea04f8`, before the first bug-fix commit `1e28c3c`.

Portal deliverables: submit the branch URL above, this root-level `submission.md`, and an actual terminal screenshot showing `git branch --show-current` and `git log --oneline --decorate -10`. `commit-history.txt` also records the branch and separate fix commits.

For the rubric's student-verification requirement, the student still needs to perform and describe a real check. One concrete check is to run `.venv/bin/python -m pytest tests/test_search.py -q`, inspect `search_songs()` in `services/search_service.py`, and compare its query with the multi-tag fixture in `tests/test_search.py`. Verify why three tag associations previously produced three results and why removing the join retains the tags while returning one song. Record the actual observation in the AI usage section after doing this; no student-run result is asserted here.
