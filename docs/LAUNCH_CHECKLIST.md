# Launch checklist

- [ ] CI passes linting, typing, unit/integration tests, browser tests, accessibility scan, dependency audit, and secret scan.
- [ ] Production has a unique persistent volume, one replica, a long random secret, HTTPS, and no time override.
- [ ] Database backup and restore have both been tested.
- [ ] `/live` and `/ready` return 200; a redeploy preserves players and game state.
- [ ] The five-player staging rehearsal is signed off on representative desktop and mobile browsers.
- [ ] Keyboard ordering, small-screen layout, login throttling, logout, and session revocation are verified.
- [ ] BBC refresh failure retains the last valid table and visibly reports stale data.
- [ ] Admin corrections, audit history, export, and one-time code rotation are verified.
- [ ] The bootstrap code has been rotated in-app and removed from deployment variables.
- [ ] A launch-day operator and rollback owner are named outside the repository.
