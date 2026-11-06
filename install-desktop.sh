#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
applications_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$applications_dir"
cat > "$applications_dir/webcorder.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Webcorder
Comment=Screen and camera recorder
Exec=$project_dir/run.sh
Icon=$project_dir/assets/webcorder-icon.png
Terminal=false
Categories=AudioVideo;Video;Recorder;
StartupWMClass=Webcorder
EOF
chmod 644 "$applications_dir/webcorder.desktop"
printf 'Installed %s\n' "$applications_dir/webcorder.desktop"
