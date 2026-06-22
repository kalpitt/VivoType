# [ADR-0005] Text-Based Multi-File Swift Build (No Xcode Project)

**Status:** Accepted  
**Date:** 2026-06-15  

## Context
The macOS client began as a single ~69 KB `clients/mac/main.swift` compiled with a one-line `swiftc main.swift` command in `build_app.sh`. As the client grew (daemon client, HUD, settings/review/onboarding windows, permissions UX), the monolith became hard to navigate and review.

The obvious industry answer is an Xcode project (`.xcodeproj`) or a Swift Package (`Package.swift`). But VivoType is deliberately built without Xcode tooling: `build_app.sh` assembles the `.app` by hand (compile, copy Resources, write `VERSION`, ad-hoc codesign) so the build is reproducible from the terminal, diffable in git, and free of the churn an `.xcodeproj` (pbxproj files, scheme management) introduces. Splitting the monolith must not sacrifice that.

## Decision
We split the client into focused single-responsibility `.swift` files in `clients/mac/` (and a `UI/` subfolder) and compile them **together as one module** with a single `swiftc -O` invocation. `build_app.sh` discovers sources dynamically (`find clients/mac -name '*.swift' -not -path './build/*'`) so new files are picked up without editing the build script.

Because all files share one module, no inter-file `import` is needed. With `main.swift` removed, top-level executable code is no longer permitted, so the process entry point is provided by the `@main` attribute on a small `VivoTypeMain` struct in `App.swift`.

## Consequences
- **Easier:** smaller, reviewable files; the build stays a transparent shell script; no IDE or generated project files to maintain or merge-conflict.
- **Harder:** no Xcode niceties (indexing, asset catalogs, scheme-based test targets); developers must add new Swift files in a location the `find` glob covers.
- **Neutral:** compile time is unchanged (still a single whole-module `swiftc` call).

## 🤖 Agent Directives
- **DO NOT** introduce an `.xcodeproj`, `.xcworkspace`, or `Package.swift` for the macOS client, and do not require Xcode.app to build it.
- **DO NOT** add top-level executable statements or re-create a `main.swift`; the single entry point is the `@main` struct in `App.swift`.
- **DO** keep new client code in focused `.swift` files under `clients/mac/` (use the `UI/` folder for window/menu code) so the `build_app.sh` glob compiles them automatically.
