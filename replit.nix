{pkgs}: {
  deps = [
    pkgs.systemd
    pkgs.libgbm
    pkgs.dbus
    pkgs.glib
    pkgs.cairo
    pkgs.pango
    pkgs.gtk3
    pkgs.cups
    pkgs.at-spi2-atk
    pkgs.alsa-lib
    pkgs.xorg.libXext
    pkgs.xorg.libXfixes
    pkgs.xorg.libXdamage
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXrandr
    pkgs.xorg.libxcb
    pkgs.xorg.libX11
    pkgs.libxkbcommon
    pkgs.expat
    pkgs.mesa
    pkgs.libdrm
    pkgs.nss
    pkgs.nspr
    pkgs.unzip
  ];
}
