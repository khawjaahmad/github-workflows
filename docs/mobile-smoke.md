# Mobile Smoke

Installs an APK on an Android emulator, launches it, reads the crash log, sends a few
thousand random UI events, and optionally runs Maestro flows. It needs a built APK and
nothing else; how the APK was built is the consumer's business.

The emulator runs on `ubuntu-latest` with KVM, which is the cheap runner. There is no iOS
path yet; the research report plans one on macOS runners as opt-in.

## Quick start

```yaml
name: Mobile Smoke

on:
  pull_request:
  merge_group:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: ./gradlew assembleDebug
      - uses: actions/upload-artifact@v4
        with:
          name: debug-apk
          path: app/build/outputs/apk/debug/app-debug.apk

  smoke:
    needs: build
    permissions:
      contents: read
    uses: khawjaahmad/github-workflows/.github/workflows/mobile-smoke.yml@v1
    with:
      app_artifact: debug-apk
      app_path: app-debug.apk
```

`app_artifact` downloads the named artifact into `mobile-app/` and `app_path` is read
inside it. Without `app_artifact`, `app_path` is a path in the checkout.

## What it does

1. Reads the application id and size from the APK with `apkanalyzer` (or `aapt2`).
2. `adb install -r`, then a launcher intent, then after `launch_wait` seconds checks the
   process is still alive and takes a screenshot.
3. Reads the crash buffer and `AndroidRuntime` errors from logcat; a fatal exception or an
   ANR fails the run.
4. `adb shell monkey` with `monkey_events` events at a fixed seed; a crash, an ANR or a
   non-zero exit fails the run. Screenshot after.
5. `maestro test` over `flows_dir` when set, with a JUnit report.
6. Dumps logcat to the artifacts.

Optionally, `cloud: firebase-robo` also runs a Firebase Test Lab Robo crawl of the same APK
with `gcloud`; the caller must have authenticated gcloud in an earlier step.

## Inputs

| Input | Default | Description |
| --- | --- | --- |
| `app_path` | _(required)_ | Path to the APK. |
| `app_artifact` | _(none)_ | Workflow artifact holding the APK (reusable workflow only). |
| `package` | from the APK | Application id. |
| `api_level` | `34` | Emulator API level. |
| `arch` / `profile` | `x86_64` / `pixel_6` | Emulator image and device profile (action only). |
| `monkey_events` | `2000` | Random events. `0` disables. |
| `flows_dir` | _(none)_ | Maestro flows. Maestro 2.10.0 is installed on demand. |
| `launch_wait` | `10` | Seconds before the alive check (action only). |
| `cloud` | `none` | `firebase-robo` to add a Test Lab crawl. |
| `fail_on` | `FAIL` | `FAIL`, or `none` to stay advisory. |

Outputs: `status` and `package`.

## Runtime and cost

Booting the emulator takes two to five minutes on a cold run; the checks themselves take
under a minute plus the monkey and the flows. Everything runs on Linux minutes.

This repository's `checks.yml` runs the action on every pull request against a fixture app,
downloaded from this repository's `test-fixtures` pre-release and pinned by SHA-256, with a
selector-free Maestro flow. The APK is kept out of git so consumers of the actions never
download it. The Python behind it is also unit-tested against a fake `adb`.
