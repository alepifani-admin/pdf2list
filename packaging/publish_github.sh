#!/usr/bin/env bash
# Pubblica PDF2LIST su GitHub e avvia la build delle tre piattaforme.
# Uso: ./packaging/publish_github.sh [owner/repo] [--public|--private]
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="${1:-pdf2list}"
VISIBILITY="${2:---public}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' pdf2list/__init__.py)"
TAG="v${VERSION}"

if ! command -v git >/dev/null 2>&1; then
  echo "git non installato."; exit 1
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) non installato."
  echo "macOS: brew install gh   Windows: winget install GitHub.cli"
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "Prima esegui: gh auth login"
  exit 1
fi

if [ ! -d .git ]; then
  git init -b main
fi

# Commit locale (se ci sono modifiche non ancora registrate)
git add -A
if ! git diff --cached --quiet; then
  git -c user.name="${GIT_AUTHOR_NAME:-PDF2LIST}" \
      -c user.email="${GIT_AUTHOR_EMAIL:-pdf2list@localhost}" \
      commit -m "PDF2LIST ${VERSION}: app desktop per listini PDF"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  gh repo create "$REPO" "$VISIBILITY" --source=. --remote=origin --push
else
  git push -u origin main
fi

if ! git rev-parse "$TAG" >/dev/null 2>&1; then
  git tag -a "$TAG" -m "PDF2LIST ${VERSION}"
fi
git push origin "$TAG"

echo
echo "Pubblicato. La build macOS Intel, macOS Apple Silicon e Windows è in corso."
echo "Segui lo stato con:"
echo "  gh run list --workflow build.yml --limit 5"
echo "  gh run watch"
echo
echo "Gli ZIP saranno disponibili in GitHub → Actions → build,"
echo "e nella release ${TAG} quando il workflow termina."
