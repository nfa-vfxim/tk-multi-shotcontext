# tk-multi-shotcontext

NFA Shot Context for Maya, as a ShotGrid Toolkit app. Adds **ShotGrid > Shot Context...** in shot tasks.

It playblasts the current shot (JPG, never submitted to ShotGrid) and plays it between the previous and the
next shot. See `python/nfa_shot_context/HELP.html` and `python/nfa_shot_context/CHANGELOG.md`.

## Config

`env/includes/app_locations.yml`:

```yaml
apps.tk-multi-shotcontext.location:
  type: github_release
  organization: nfa-vfxim
  repository: tk-multi-shotcontext
  version: 1.1.0
```

`env/includes/settings/tk-maya.yml`, under `settings.tk-maya.shot_step` → `apps`:

```yaml
    tk-multi-shotcontext:
      location: "@apps.tk-multi-shotcontext.location"
```

Release first, then bump the config (a config pointing at a missing release breaks the Maya menu).

v1.1.0 · Luuk Kamphuis
