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

```sh
npx @marp-team/marp-cli@latest igate-talk.md --pdf          # for projecting
npx @marp-team/marp-cli@latest igate-talk.md --pptx         # if the venue wants PowerPoint
npx @marp-team/marp-cli@latest igate-talk.md --html         # self-contained page
```

In VS Code the same exports are under *Marp: Export Slide Deck* in the command
palette.

## Images

`img/` holds anything made specifically for the talk. Figures and screens that
already live elsewhere in this repository are referenced in place rather than
copied, so they stay in step with the project.
