# Pubblicare PDF2LIST su GitHub

La build delle tre piattaforme è automatizzata con GitHub Actions:

| Runner | Artefatto |
|---|---|
| `macos-15-intel` | `PDF2LIST-macOS-intel.zip` |
| `macos-15` (Apple Silicon) | `PDF2LIST-macOS-apple-silicon.zip` |
| `windows-latest` | `PDF2LIST-Windows-x64.zip` |

Il workflow `.github/workflows/build.yml` esegue i test, compila con
PyInstaller e allega gli ZIP alla release quando viene creato un tag `v*`.

## 1. Prerequisiti

- Un account GitHub.
- Git installato.
- GitHub CLI `gh` installata e autenticata:

```bash
# macOS
brew install gh
# Windows (PowerShell)
winget install GitHub.cli

gh auth login
```

Se Homebrew non installa `gh` su un Mac Intel, scarica `gh_<versione>_macOS_amd64.zip`
dalla pagina Releases di `cli/cli` e copia il binario `gh` in `~/.local/bin`.

## 2. Pubblicazione automatica

Dalla cartella del progetto:

```bash
./packaging/publish_github.sh pdf2list --public
```

Oppure, per un repository privato:

```bash
./packaging/publish_github.sh pdf2list --private
```

Se vuoi pubblicare sotto un owner/organizzazione specifica:

```bash
./packaging/publish_github.sh mio-utente/pdf2list --public
```

Lo script:

1. inizializza il repository locale se necessario;
2. crea il commit;
3. crea il repository GitHub e pusha `main`;
4. crea il tag della versione (es. `v1.1.0`) e lo pusha;
5. avvia il workflow di build.

Segui la build:

```bash
gh run list --workflow build.yml --limit 5
gh run watch
```

Al termine trovi gli ZIP:

- in **GitHub → Actions → build → Artifacts**;
- nella pagina **Releases** del tag, se il workflow è partito da un tag.

## 3. Pubblicazione manuale

```bash
git init -b main
git add -A
git commit -m "PDF2LIST 1.1.0"
gh repo create pdf2list --public --source=. --remote=origin --push
git tag -a v1.1.0 -m "PDF2LIST 1.1.0"
git push origin v1.1.0
```

## 4. Cosa non finisce nel repository

Per evitare di pubblicare il listino originale e i file pesanti, `.gitignore`
esclude:

- `source.pdf`, `pages/`, `parsed.json`, `pending*.json`;
- `parse.py`, `excel.py`, i sorgenti/binari OCR Swift;
- gli output Excel/CSV di esempio nella root;
- `.venv-app/`, `.venv-build/`, `build/`, `dist/`, `release/`.

Il codice dell'app, i profili, i test, la documentazione e i workflow sono
inclusi. Le chiavi API non sono mai salvate nei file del progetto.

## 5. Firma e notarizzazione (opzionale)

Le build macOS sono firmate ad-hoc e non notarizzate. Per evitare l'avviso
Gatekeeper a tutti gli utenti servono:

- un certificato Apple Developer ID Application;
- la notarizzazione con `notarytool`;
- la modifica del workflow per importare il certificato e fare `codesign` +
  `xcrun notarytool submit`.

Senza certificato, l'utente deve fare **clic destro → Apri** al primo avvio.

Per Windows, per evitare SmartScreen serve un certificato di code signing e la
firma dell'eseguibile con `signtool`; in alternativa l'utente sceglie
**Ulteriori informazioni → Esegui comunque**.
