#!/bin/bash
# Make the Flatpak repository players install from, as a static site:
#
#   app/linux/publish.sh SITE ARCH_REPO...
#
# Each ARCH_REPO is an OUT/repo of build-flatpak.sh (one an architecture).
# SITE gets:
#   repo/                                   the OSTree repository, signed
#   io.cpressland.ThemeParkWorld.flatpakref one-click install (and the remote)
#   theme-park-world.flatpakrepo            the remote alone
#   index.html                              how to install
# The repository holds the latest release only: an update pulls what changed,
# and static deltas make a fresh install one download.
#
# FLATPAK_GPG_PRIVATE_KEY: the armoured signing key (its public half is
# flatpak-signing-key.asc). FLATPAK_URL: where SITE is served
# (https://flatpak.cpressland.io).
set -euo pipefail

[ $# -ge 2 ] || { sed -n '2,17p' "$0"; exit 2; }
: "${FLATPAK_GPG_PRIVATE_KEY:?the signing key}"
url=${FLATPAK_URL:-https://flatpak.cpressland.io}
url=${url%/}
app=io.cpressland.ThemeParkWorld
site=$1
shift

gpghome=$(mktemp -d)
trap 'rm -rf "$gpghome"' EXIT
chmod 700 "$gpghome"
printf '%s\n' "$FLATPAK_GPG_PRIVATE_KEY" | gpg --homedir "$gpghome" --batch --quiet --import
key=$(gpg --homedir "$gpghome" --list-secret-keys --with-colons | awk -F: '/^fpr/{print $10; exit}')
pubkey=$(gpg --homedir "$gpghome" --export "$key" | base64 | tr -d '\n')

rm -rf "$site"
mkdir -p "$site"
ostree init --mode=archive-z2 --repo="$site/repo"
for r in "$@"; do
  for ref in $(ostree refs --repo="$r" | grep "^app/$app/"); do        # the app, not its Debug extension
    ostree pull-local --repo="$site/repo" "$r" "$ref"
    IFS=/ read -r _ _ arch branch <<< "$ref"
    flatpak build-sign "$site/repo" "$app" "$branch" --arch="$arch" --gpg-sign="$key" --gpg-homedir="$gpghome"
    echo "$ref"
  done
done
flatpak build-update-repo --gpg-sign="$key" --gpg-homedir="$gpghome" --generate-static-deltas \
  --title="Theme Park World" --default-branch=stable "$site/repo"

cat > "$site/$app.flatpakref" <<EOF
[Flatpak Ref]
Name=$app
Branch=stable
Title=Theme Park World
Url=$url/repo/
SuggestRemoteName=theme-park-world
RuntimeRepo=https://dl.flathub.org/repo/flathub.flatpakrepo
IsRuntime=false
Homepage=https://github.com/cpressland/tpw-recomp
GPGKey=$pubkey
EOF
cat > "$site/theme-park-world.flatpakrepo" <<EOF
[Flatpak Repo]
Title=Theme Park World
Url=$url/repo/
Homepage=https://github.com/cpressland/tpw-recomp
GPGKey=$pubkey
EOF
cat > "$site/index.html" <<EOF
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Theme Park World for Linux</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:40em;margin:3em auto;padding:0 1em}code,pre{background:#f2f0f7;border-radius:4px}pre{padding:.8em;overflow-x:auto}</style>
</head><body>
<h1>Theme Park World for Linux</h1>
<p>A Flatpak for any Linux, Steam Deck included. Its first run downloads the game and builds it on your computer, which takes 10 to 20 minutes.</p>
<p><a href="$app.flatpakref">Install Theme Park World</a> (opens your software centre), or from a terminal:</p>
<pre>flatpak install --user $url/$app.flatpakref</pre>
<p>On a Steam Deck: in Desktop Mode, run that in Konsole, then add Theme Park World to Steam (Add a Non-Steam Game) to play it from Game Mode.</p>
<p>Updates come with your other Flatpaks. Source: <a href="https://github.com/cpressland/tpw-recomp">github.com/cpressland/tpw-recomp</a>.</p>
</body></html>
EOF
echo "==> $site ($url)"
