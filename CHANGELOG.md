# Changelog

## [1.0.0](https://github.com/SimAin/gh-issue-check/compare/v0.5.0...v1.0.0) (2026-07-19)


### ⚠ BREAKING CHANGES

* **config:** ~/.config/issue-check/config.json and $ISSUE_CHECK_CONFIG are no longer read; use ~/.config/plate/config.json or $PLATE_CONFIG.
* the distribution and import package are renamed and the issue-check command is removed (hard switch, epic #50 decision 2); use `plate issues`.
* **cli:** the issue-check command is gone; use `plate issues`.

### Features

* **cli:** introduce plate CLI with issues subcommand ([129ddbe](https://github.com/SimAin/gh-issue-check/commit/129ddbedad8154cf38611a2407fd4f4febf8df7c))
* **config:** move config to ~/.config/plate (hard switch) ([28e0219](https://github.com/SimAin/gh-issue-check/commit/28e021985eeed25a36344a4d75772016242d80cb))


### Code Refactoring

* restructure into plate/core and plate/issues packages ([70a2b00](https://github.com/SimAin/gh-issue-check/commit/70a2b00bcc200b88d37924f93a49459acb85ee05))

## [0.5.0](https://github.com/SimAin/gh-issue-check/compare/v0.4.1...v0.5.0) (2026-07-16)


### Features

* **cli:** add owner-wide issue view via --owner ([48c465b](https://github.com/SimAin/gh-issue-check/commit/48c465ba6becc4bc2bbed46f11e5bf004ada973d))
* **cli:** add owner-wide issue view via --owner ([2e7c010](https://github.com/SimAin/gh-issue-check/commit/2e7c01069f9601472c1f6502511c2be6ca8460a6)), closes [#43](https://github.com/SimAin/gh-issue-check/issues/43)

## [0.4.1](https://github.com/SimAin/gh-issue-check/compare/v0.4.0...v0.4.1) (2026-07-14)


### Bug Fixes

* **sprint:** give --show-key a sprint-specific key ([7acfcb8](https://github.com/SimAin/gh-issue-check/commit/7acfcb86d9795fdce3faaca2c881eb4bf9de379e))
* **sprint:** honour "hide" label styles on others/unassigned rows ([0e3db7c](https://github.com/SimAin/gh-issue-check/commit/0e3db7c530bee1cde9d66ed9b993a035e4e87980))
* **sprint:** match and validate statusOrder against what the user sees ([2306473](https://github.com/SimAin/gh-issue-check/commit/2306473f58cf9382abe61e7ecc76c2c1a0f74ce3))
* **sprint:** match repos config keys case-insensitively ([82ffdfd](https://github.com/SimAin/gh-issue-check/commit/82ffdfd283f2130f27f412bd2dbf953da9a9ea92))

## [0.4.0](https://github.com/SimAin/gh-issue-check/compare/v0.3.0...v0.4.0) (2026-07-14)


### Features

* current-sprint board data layer and model ([c5585c4](https://github.com/SimAin/gh-issue-check/commit/c5585c4512a9dc37a4a5eb8579a8955c2fda5120))
* per-repo project board config ([20e5aef](https://github.com/SimAin/gh-issue-check/commit/20e5aefe4750bc729e3bb9a1738d2d47d5e068c9))
* sprint view (--sprint) ([0a7290b](https://github.com/SimAin/gh-issue-check/commit/0a7290b8203c7ebf3fb02d96a58c4dd6fe1d92f9))
* sprint view rendering and --sprint CLI ([ac99436](https://github.com/SimAin/gh-issue-check/commit/ac9943677e5d3c27a29e1cca86778588fefcbef3))


### Bug Fixes

* **sprint:** validate configured board fields before fetching items ([740c9bb](https://github.com/SimAin/gh-issue-check/commit/740c9bbd78ee3f15f9241d3e3ca9f7a4b58deb3e))


### Documentation

* sprint view (SPRINT.md, README, decision log) ([f0135e5](https://github.com/SimAin/gh-issue-check/commit/f0135e587592ad05941c482c5e46117f78a7ba2a))

## [0.3.0](https://github.com/SimAin/gh-issue-check/compare/v0.2.0...v0.3.0) (2026-07-14)


### Features

* add a year unit to format_age ([8fb2b55](https://github.com/SimAin/gh-issue-check/commit/8fb2b55b1c51a23bde5d91ca3d5f7be246ee6b7c))


### Bug Fixes

* print the truncation note to stderr, not stdout ([e019460](https://github.com/SimAin/gh-issue-check/commit/e01946038bb371178cf4f8c29ce2ef7db1f929e6))
* raise a clean IssueCheckError when gh or git is not installed ([b16c366](https://github.com/SimAin/gh-issue-check/commit/b16c366cbf141c8043c7d4241ea76f5b13f6f90b))
* reject non-positive --limit and --stale-days at argparse ([e0aa9cd](https://github.com/SimAin/gh-issue-check/commit/e0aa9cd5dd4eeb9a5a51f250c1b4b2a1591bff9d))


### Documentation

* cite the real 3.11 Python floor in config.py and D5 ([023ea7d](https://github.com/SimAin/gh-issue-check/commit/023ea7de28106758d1ad48e3a363ee96de041fe2))

## [0.2.0](https://github.com/SimAin/gh-issue-check/compare/v0.1.0...v0.2.0) (2026-07-14)


### Features

* add --version flag ([5071548](https://github.com/SimAin/gh-issue-check/commit/507154866dd10cc1586190a570c96ac5fb9c3971))
* add --version flag ([2ca613d](https://github.com/SimAin/gh-issue-check/commit/2ca613d95b7cb0e7ecb860d28098bcbca911e04e))
* configurable special-label highlighting ([17e43e0](https://github.com/SimAin/gh-issue-check/commit/17e43e0088fd4c9e52be4f1f3c37dec64692cc1c))
* domain model and GitHub data layer ([629829b](https://github.com/SimAin/gh-issue-check/commit/629829ba7d7606420e5017c3aa404d98af2404e1))
* terminal/markdown rendering and CLI ([38a5bea](https://github.com/SimAin/gh-issue-check/commit/38a5bead6c781f508ac8ba2b186c7c1356e74758))


### Documentation

* README, MVP slice writeup, and decision log ([05ad3be](https://github.com/SimAin/gh-issue-check/commit/05ad3bed9d229f8da44b2b7fa4783060915e1c0d))
