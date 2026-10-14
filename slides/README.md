# iGate club talk

`igate-talk.md` — a [Marp](https://marp.app) deck, 13 slides, about 10–13 minutes for a
club audience.

Assertion-evidence: every headline is a full sentence making a claim, and the body
of the slide is the evidence for that claim. There are no bullet-point agendas.

## Previewing it

The styling lives in the deck's own front matter, so there is nothing to register
or configure.

- **VS Code** — install the *Marp for VS Code* extension (`marp-team.marp-vscode`)
  and open the file. The preview pane renders it.
- **Command line** — `npx @marp-team/marp-cli@latest igate-talk.md --preview`

## Exporting it

### From the command line on this laptop

There is no `node`, `npx` or `marp` on PATH here, and installing Node is not
necessary: **VS Code ships a Node runtime, and the Marp extension ships
marp-cli.** Together they build the deck with nothing else installed.

```sh
cd slides
ELECTRON_RUN_AS_NODE=1 /usr/share/code/code \
  ~/.vscode/extensions/marp-team.marp-vscode-3.6.1/node_modules/@marp-team/marp-cli/marp-cli.js \
  igate-talk.md --pdf --allow-local-files
```

Verified 2026-10-13: 17 pages, 960x540. Swap `--pdf` for `--pptx` or `--html`.

`ELECTRON_RUN_AS_NODE=1` makes the Electron binary behave as plain `node` —
`/usr/share/code/code -e 'console.log(process.version)'` prints v24.18.1 under
it. Note it must be the real binary at `/usr/share/code/code`; the `/usr/bin/code`
wrapper re-execs and ignores the variable, so it just prints the VS Code
version instead.

The extension version is in that path, so **bump it after an extension
update** — or find it with:

```sh
ls -d ~/.vscode/extensions/marp-team.marp-vscode-*/node_modules/@marp-team/marp-cli/marp-cli.js
```

`--allow-local-files` is required because the deck references images from
elsewhere in the repository rather than copying them.

### In the editor

*Marp: Export Slide Deck* in the command palette does the same thing.

### Upstream instructions, for a machine with Node

```sh
npx @marp-team/marp-cli@latest igate-talk.md --pdf          # for projecting
npx @marp-team/marp-cli@latest igate-talk.md --pptx         # if the venue wants PowerPoint
npx @marp-team/marp-cli@latest igate-talk.md --html         # self-contained page
```

The `marpteam/marp-cli` container is not worth trying: it writes output as a
UID that does not map to the caller, so the export fails with `EACCES` on the
mounted directory even with `--userns=keep-id`.

**The PDF is a build artifact and goes stale silently.** It is committed, so
compare `git log --oneline -1 -- slides/igate-talk.pdf` against
`git log --oneline -1 -- slides/igate-talk.md`.

## Images

`img/` holds anything made specifically for the talk. Figures and screens that
already live elsewhere in this repository are referenced in place rather than
copied, so they stay in step with the project.
