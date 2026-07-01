# Project 5: Mixtape Bug Hunt (Submission)

## Codebase Map

*(Written during orientation, before any bug work.)*

Mixtape is a Flask + SQLAlchemy REST API for a social music app. It follows a clean
three-layer architecture: **routes** (HTTP) then **services** (business logic) then **models**
(persistence). Every route is a thin wrapper that parses request input, calls exactly one
service function, and formats the JSON response. All real logic lives in `services/`, which
is also where all five bugs live.

### Main files and their roles

**`app.py`**: The application factory. `create_app(config=None)` builds the Flask app,
configures the SQLAlchemy database (defaults to `sqlite:///mixtape.db`), initializes the
shared `db` object, registers the four blueprints under their URL prefixes (`/songs`,
`/playlists`, `/users`, `/feed`), and calls `db.create_all()`. The `db = SQLAlchemy()`
instance defined here is imported by every other module. The app must be started with
`FLASK_APP=app:create_app flask run`; running `python app.py` triggers a double-import of
the models and a SQLAlchemy error.

**`models.py`**: Defines all persistence. Seven mapped entities and three association tables:

- **Entities:** `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`.
- **Association tables:**
  - `friendships`: self-referential many-to-many on `User`, stored *symmetrically* (the seed
    inserts both directions, e.g. nova to darius *and* darius to nova).
  - `song_tags`: many-to-many between `Song` and `Tag`.
  - `playlist_entries`: many-to-many between `Playlist` and `Song`, but it carries extra
    columns: **`position`** (explicit ordering, so songs have a defined order, not just insertion
    order), `added_by`, and `added_at`. This is the join table equivalent of the "PlaylistSong"
    concept.
- Key relationships: `User.friends` is a `lazy="dynamic"` self-join; `Song.tags` and
  `Playlist.songs` use `secondary` association tables with `lazy="subquery"`.
- Ratings are their own model (`Rating`, score 1 to 5) with a `UniqueConstraint(user_id, song_id)`,
  so a user can rate a song only once, but can update that rating.
- Each model has a `to_dict()` used by the services to build JSON responses. `Song.to_dict()`
  flattens tags into `[tag.name for tag in self.tags]`.

**`routes/`**: The HTTP layer. Four blueprints:

- `songs.py`: `GET /songs/search?q=`, `GET /songs/<id>`, `POST /songs/<id>/rate`,
  `POST /songs/<id>/listen`.
- `playlists.py`: `POST /playlists/`, `GET /playlists/<id>`, `GET /playlists/<id>/songs`,
  `POST /playlists/<id>/songs`.
- `users.py`: `GET /users/<id>`, `GET /users/<id>/streak`, `GET /users/<id>/notifications`,
  `POST /users/notifications/<id>/read`.
- `feed.py`: `GET /feed/<user_id>/listening-now`, `GET /feed/<user_id>/activity`.

Routes never touch the database directly (with the minor exception of `users.get_user`, which
does a simple `db.session.get`). They catch `ValueError` from services and turn it into a 400
or 404 response.

**`services/`**: All business logic. One module per feature area:

- `streak_service.py`: records listening events and maintains each user's consecutive-day
  `listening_streak`. Core logic is `update_listening_streak(user, now)`.
- `feed_service.py`: `get_friends_listening_now()` (recency-filtered, deduped to the most
  recent song per friend) and `get_activity_feed()` (most recent N events, no recency filter).
  `RECENT_THRESHOLD` defines the "listening now" window.
- `search_service.py`: `search_songs(query)` matches title/artist case-insensitively via an
  `outerjoin` onto `song_tags`; `get_song(id)` fetches one song.
- `notification_service.py`: `create_notification()` (the shared helper), `add_to_playlist()`
  (adds a song to a playlist *and* notifies the sharer), `rate_song()` (saves/updates a rating),
  `get_notifications()`, `mark_as_read()`.
- `playlist_service.py`: `create_playlist()`, `get_playlist_songs()` (ordered by
  `playlist_entries.position`), `get_playlist()`, `get_user_playlists()`.

**`seed_data.py`**: Drops and recreates all tables, then populates realistic test data: 5
users with bidirectional friendships, 25 songs deliberately split into 0-tag, 1-tag, and 3+-tag
groups (the multi-tag songs are what expose the search-duplication bug), 3 playlists with
overlapping song sets, recent (under 30 min) and older (1 to 14 day) listening events, some
pre-set streaks, and one example "song added to playlist" notification.

**`tests/`**: `test_streaks.py`, `test_search.py`, `test_playlists.py`. Several test cases
already assert the *correct* post-fix behavior (e.g. `test_streak_increments_on_sunday`,
`test_search_no_duplicates_multi_tag_song`, `test_playlist_returns_all_songs`), so they fail
against the current buggy code and serve as ready-made reproductions.

### Data flow: sharing/adding a song triggers a notification

Take "a friend adds my song to a playlist" (`POST /playlists/<playlist_id>/songs`):

1. **Route** (`routes/playlists.py`, `add_song`): parses `song_id` and `added_by` from the
   JSON body, validates both are present, and calls
   `notification_service.add_to_playlist(playlist_id, song_id, added_by)`.
2. **Service** (`notification_service.add_to_playlist`): loads the `Song`, the adding `User`,
   and the `Playlist` (raising `ValueError` if any is missing). If the song isn't already in
   the playlist, it appends it (`playlist.songs.append(song)`) and commits.
3. **Notification step:** if the adder is *not* the original sharer
   (`song.shared_by != added_by_user_id`), it calls `create_notification(user_id=song.shared_by,
   type="song_added_to_playlist", body="...")`, which inserts a `Notification` row addressed to
   the original sharer and commits.
4. **Retrieval:** later, `GET /users/<sharer_id>/notifications` calls `get_notifications()`, which
   queries `Notification` rows for that user, newest first, and returns them via `to_dict()`.

The parallel "rate a song" flow (`POST /songs/<id>/rate`, `rate_song()`) follows the same
route-to-service shape and stores a `Rating`, but notably does *not* create a notification the
way `add_to_playlist` does.

### Patterns I noticed

- **Strict route/service separation.** Routes do input parsing and response formatting only;
  every route delegates business logic to a single service call. Services raise `ValueError`
  for "not found" / invalid input, and routes translate those into HTTP 400/404.
- **`db.session.get(Model, id)` plus a `ValueError` guard** is the standard "load or fail" idiom,
  repeated at the top of nearly every service function.
- **`to_dict()` on models** is the single serialization boundary: services return lists/dicts
5  of primitives, never ORM objects, so the JSON shape is controlled in one place per model.
- **Ordering is explicit, not implicit.** `playlist_entries.position` means playlist order is
  a stored integer, and `get_playlist_songs` sorts by it rather than trusting insertion order.
- **Friendships are stored symmetrically** in the seed data, so a one-directional query over
  `user.friends` still finds friends in both directions.
- **The bugs are all in `services/`**, and the tests are written against the intended behavior,
  which makes them useful reproduction harnesses rather than passing checks.

---

## Root Cause Analysis

### Issue #1: My listening streak keeps resetting

**How I reproduced it.** I ran the existing streak test suite with `pytest tests/test_streaks.py -v`.
Four tests passed but `test_streak_increments_on_sunday` failed with `assert 1 == 2`: the test
listens on Saturday (2024-06-15) then Sunday (2024-06-16) and expects the streak to reach 2, but
it stayed at 1. That pinned the trigger condition precisely: the reset only happens when the
second listen lands on a **Sunday**.

**How I found the root cause.** Following the brief's "trace from the route, don't skip to the
service" guidance, I started in `routes/songs.py` and found the `POST /songs/<id>/listen` route,
which calls `record_listening_event` from `streak_service`. That function does no streak math
itself; it delegates to `update_listening_streak(user, now)`. Reading that function, the
increment/reset decision is a single `if/elif/else` block (lines 70 to 78). The moment I was
confident I'd found the cause was seeing the extra clause on the increment branch:
`elif days_since_last == 1 and today.weekday() != 6:`. Nothing else in the function depends on
the weekday, so that clause was the only thing that could make Sunday behave differently.

**The root cause.** Python's `datetime.weekday()` returns 6 for Sunday (Monday is 0). The
increment branch required `days_since_last == 1 and today.weekday() != 6`. On any Sunday,
`today.weekday() != 6` is `False`, so a genuinely consecutive Saturday-to-Sunday listen fails the
`elif` and falls through to the `else`, which resets the streak to 1. A streak should increment on
*any* consecutive calendar day, so the day of the week is irrelevant; the weekday condition had no
valid reason to exist and silently broke every streak that crossed into a Sunday.

**My fix and side-effect check.** I removed the `and today.weekday() != 6` clause, leaving
`elif days_since_last == 1:`. That restores the intended rule: exactly one day since the last
listen increments, anything else resets. After the fix all 5 streak tests pass. The check I cared
about most was `test_streak_resets_after_skipped_day`, because my edit sits directly beside the
reset path: I confirmed a skipped day still resets to 1, so I only re-enabled the legitimate
Sunday increment without weakening the reset logic. I also confirmed the same-day
(no-double-count) and new-user paths still behave correctly.

### Issue #5: The last song in a playlist never shows up

**How I reproduced it.** I ran `pytest tests/test_playlists.py -v`. The fixture builds a playlist
with 5 songs at positions 1 to 5. Two tests failed: `test_playlist_returns_all_songs` (expected 5,
got 4) and `test_playlist_returns_songs_in_order`, which reported the returned titles as
`['Track 1', 'Track 2', 'Track 3', 'Track 4']` with pytest noting *"Right contains one more item:
'Track 5'"*. `test_empty_playlist_returns_empty_list` passed. That told me the last element of a
non-empty, position-ordered list was being dropped.

**How I found the root cause.** I traced from `GET /playlists/<id>/songs` in `routes/playlists.py`,
which calls `get_playlist_songs` in `playlist_service.py`. Reading that function, the SQL query is
correct: it joins `playlist_entries`, filters by playlist, and orders ascending by
`playlist_entries.position`, so it fetches all songs in order. The bug had to be after the query.
The `return` statement on line 66 was the giveaway: `return [song.to_dict() for song in songs[:-1]]`.
The `[:-1]` slice is the only thing between a correct query and a short result.

**The root cause.** The list comprehension iterates over `songs[:-1]` instead of `songs`. The
slice `[:-1]` returns every element except the last. Because the query orders songs ascending by
`position`, the excluded element is always the highest-position song, so every non-empty playlist
silently loses its final track. Empty playlists are unaffected because slicing an empty list still
yields an empty list, which is why `test_empty_playlist_returns_empty_list` passed and masked the
severity.

**My fix and side-effect check.** I changed the return to iterate over the full list:
`return [song.to_dict() for song in songs]`. After the fix all 3 playlist tests pass, including the
empty-playlist case, confirming I didn't break the empty path. I also grepped the codebase for
other callers of `get_playlist_songs`: the only functional caller is the GET route, which is meant
to display the whole playlist, so restoring all songs is exactly what it wants. `notification_service`
imports the function inside `add_to_playlist` but never calls it (it uses the `playlist.songs`
relationship for its membership check), so no other behavior depended on the truncated result.

### Issue #4: I got notified when a friend added my song to a playlist but not when they rated it

**How I reproduced it.** There was no existing test for this, so I wrote a short script that creates
a sharer and a separate rater, has the rater call `rate_song(rater, song, 5)` on the sharer's song,
then calls `get_notifications(sharer)`. The result was 0 notifications: rating a song produced no
notification for the sharer, while the "added to playlist" flow does. (I later turned this into the
regression test described below.)

**How I found the root cause.** Following the brief's hint that this is architectural rather than a
typo, I traced both parallel actions from their routes: `POST /songs/<id>/rate` in `routes/songs.py`
calls `rate_song`, and `POST /playlists/<id>/songs` in `routes/playlists.py` calls `add_to_playlist`
(both in `notification_service.py`). I read the two functions side by side. `add_to_playlist` ends
with a clear notification block: `if song.shared_by != added_by_user_id: create_notification(...)`.
`rate_song` had no equivalent block at all: it validated the score, saved or updated the `Rating`,
committed, and returned. The moment of confidence was seeing that the working function had a whole
"notify the sharer" step that the broken one was simply missing.

**The root cause.** `rate_song` never called `create_notification`. The rating was persisted
correctly, but the code path that turns "someone interacted with your song" into a `Notification`
row existed only for playlist adds, not for ratings. This is a missing feature/step, not a wrong
comparison, which is why the fix mirrors an existing pattern rather than correcting a line.

**My fix and side-effect check.** After `rate_song` commits the rating, I added the same notify
pattern used by `add_to_playlist`: if the rater is not the song's sharer, create a `song_rated`
notification addressed to `song.shared_by`. I guarded on `song.shared_by != user_id` so that rating
your own song does not notify yourself. Side-effect checks: (1) a different user rating a song now
yields exactly one `song_rated` notification with the correct body; (2) a user rating their own song
yields zero notifications, confirming the self-rating guard; (3) the full test suite (15 tests)
still passes, so the added commit path did not disturb rating persistence or the existing
playlist-add notification. I also confirmed the notification type string (`song_rated`) matches the
type named in `create_notification`'s own docstring, keeping it consistent with the existing
convention.

**Regression test.** `tests/test_notifications.py` contains `test_rating_a_song_notifies_the_sharer`,
which rates another user's song and asserts the sharer receives exactly one `song_rated`
notification. Against the original code this assertion fails (the count is 0), so the test would
have caught the bug before it shipped. A companion test,
`test_rating_your_own_song_does_not_notify`, locks in the self-rating guard.
