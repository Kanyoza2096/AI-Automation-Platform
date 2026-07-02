---
name: Playwright NixOS system dependencies
description: Nix packages required for Playwright Chromium headless shell to run on Replit's NixOS environment
---

## Rule
Installing `playwright` (pip) and running `playwright install chromium` downloads the binary but does NOT install its system-level `.so` libraries. On NixOS (Replit), `playwright install-deps` fails because it tries to use apt. Must manually install via `installSystemDependencies()`.

**Why:** NixOS doesn't have /lib or /usr/lib in standard paths; shared libraries must be explicitly pulled into the Nix store.

**How to apply:** After any fresh Playwright install, run `ldd <chromium-binary> | grep "not found"` to see what's missing, then install the Nix packages below.

## Confirmed working Nix packages (as of July 2026)

First batch (install together):
```
nspr, nss, libdrm, mesa, expat, libxkbcommon,
xorg.libX11, xorg.libxcb, xorg.libXrandr,
xorg.libXcomposite, xorg.libXdamage, xorg.libXfixes,
xorg.libXext, alsa-lib, at-spi2-atk, cups,
gtk3, pango, cairo, glib
```

Second batch (still missing after first):
```
dbus, libgbm, systemd
```

After installing both batches, `ldd | grep "not found"` returns nothing (all resolved).

## Binary path
`<workspace>/.cache/ms-playwright/chromium_headless_shell-<version>/chrome-headless-shell-linux64/chrome-headless-shell`

## Playwright launch flags (required on Replit)
```python
browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
```
