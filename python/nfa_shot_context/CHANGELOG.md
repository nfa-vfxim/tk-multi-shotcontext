# NFA Shot Context — Changelog

## [1.2.0] — 2026-10-23 (Luuk)
- Choose a version per shot: the ▾ button on each shot in the bar (or right-click) lists every playblast of that
  shot (ShotGrid and Maya folders, newest first) plus the footage; your own shot also lists your earlier takes.
  Only the version you pick is loaded (decoded / downloaded on demand).
- Removed the Re-playblast button: restart the tool instead (that's how animators work).
- Runs as Toolkit app `tk-multi-shotcontext` (ShotGrid > Shot Context...).

## [1.1.0] — 2026-10-23 (Luuk)
- Same black look as the Review tool's playblast preview instead of the purple NFA palette: black rounded
  viewport with a play badge when paused, white round play button, blue accent, transport and footer bars,
  chips for project / shot / step / fps, the same vector icons. Help page in the same colours.

## [1.0.0] — 2026-10-23 (Luuk)
First version.

- One click from the NFA shelf: playblasts the current shot (JPG, never submitted to ShotGrid) and plays it
  between the previous and the next shot.
- ShotGrid only: refuses to start without a Toolkit engine or outside a Shot context.
- Always three shots; the window shifts at the first and last shot of the film.
- Shot order: sequence, then shot code (natural sort), across sequences. Omitted shots (`omt`) are skipped.
- Neighbour shots show, in this order:
  1. the newest playblast — newest ShotGrid playblast Version or newest movie / image sequence in
     `<shot>/<step>/maya/previews`, whichever is newer;
  2. footage — `02_source/sc<seq>/<shot>/p###/plates/ref/*.mov` (highest p-version), a published
     plate/footage movie, or a JPG/PNG plate sequence;
  3. "NO FOOTAGE FOUND" card for the shot's ShotGrid duration (cut duration, cut in/out, or EXR plate frame count);
  4. "NO FOOTAGE" card of 2 seconds.
- Playblasts go to `<step>/maya/previews/shot_context/<shot>_shotcontext_<date>_<user>/`; the newest 3 per
  artist are kept. Falls back to a local temp folder (with a warning) when the shot folder isn't writable.
- Movies are decoded once with QtMultimedia into `%TEMP%\nfa_shot_context\cache` (frame accurate, LRU-trimmed at 3 GB).
- Viewer: one timeline for the three shots, scrubbing, frame stepping, shot jumping, loop, mute,
  Re-playblast, source and age of each neighbour playblast in the picture.
- Sound: scene sound for your shot, the movie's own sound for neighbours. `NFA_SHOT_CONTEXT_NO_AUDIO=1` disables it.
- Shelf: button in `shelf_NFA.mel` (Maya 2025/2026) plus `ensure_shelf_button()` from `userSetup.mel`, because Maya
  keeps a per-user copy of the NFA shelf in the prefs and would never show a newly added button to existing users.
