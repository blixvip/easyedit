# Design — easyedit

> **Not in use.** On 2026-10-02 kaigu chose to keep the After Effects look (neutral grey panels, blue accent,
> Segoe UI) for both the projects page and the editor. The Hallmark layers this file describes were removed from
> `easyedit/web/index.html` and `easyedit/web/editor.html`. Do not re-apply this system unless he asks for it.

A locked design system for this app. Every page reads this file before it is restyled.
Amend this file when the system needs to grow; do not restyle one page on its own.

## Genre
atmospheric

## Macrostructure family
- App pages: Portfolio Grid for the projects page (`easyedit/web/index.html`), Workbench for the editor (`easyedit/web/editor.html`).
- There are no marketing or content pages. If one is added, start it from Marquee Hero.

## Theme
Custom, tuned to the yellow in the easyedit wordmark. Vibe: late-night cutting room, poster type, one yellow light.
Axes: dark paper / display-condensed / warm accent.

- `--color-paper`   oklch(15% 0.008 88)
- `--color-paper-2` oklch(19% 0.010 88)
- `--color-paper-3` oklch(23.5% 0.011 88)
- `--color-paper-4` oklch(29% 0.012 88)
- `--color-rule`    oklch(34% 0.010 88)
- `--color-ink`     oklch(95% 0.008 92)
- `--color-accent`  oklch(89% 0.125 96)
- `--color-focus`   oklch(86% 0.15 92)

## Typography
- Display: Anton, weight 400, upright, uppercase, tracking 0.008em to 0.02em. Used for page titles and the film's name only.
- Body: Geist, 400 / 500 / 600.
- Labels: Geist Mono, 400 / 500, uppercase with 0.06em to 0.1em tracking for tags, tabs and readouts; plain for numbers and timecode.
- Type scale anchor: `--text-display` = clamp(2.75rem, 4.2vw + 1rem, 5rem).
- No italic headings anywhere.

## Spacing
4-point named scale in `tokens.css` (`--space-3xs` to `--space-2xl`). Use the names, not raw values.

## Motion
- Easings: `--ease-out` cubic-bezier(0.16, 1, 0.3, 1), `--ease-in`, `--ease-in-out`.
- Fade only for things that appear. Lift (translateY) only on project pictures.
- Timeline dragging uses the page's own spring; it is direct manipulation, not decoration.
- Reduced motion: everything collapses to an instant state.

## Microinteractions stance
- Buttons answer on press with a 1px drop.
- The focus ring is a 2px yellow outline and never animates in.
- Success is quiet: a short toast, no celebration.
- Reversible actions use undo (Ctrl+Z), not confirmation dialogs.

## CTA voice
- Primary: one per page, a yellow pill (`Make it`, `Export`).
- Secondary: ink-on-paper fill that turns yellow on hover.
- Everything else: a paper-3 block, 6 to 8px radius.

## Surfaces
- No hairline borders and no glass. Surfaces separate by lightness: paper, then paper-2, paper-3, paper-4.
- At most two warm blooms on the projects page background. None in the editor.
- The accent stays on small things: the playhead, the selection ring, the active tab line, the focus ring, one button.

## What pages MUST share
- The wordmark, the yellow and where it is allowed, the three typefaces, the button shapes, the tab style (mono uppercase with a yellow line under the current one).

## What pages MAY differ on
- Density. The projects page is roomy; the editor is tight.
- The projects page may use the display face at poster size; the editor uses it only for the film's name and the busy screen.

## Known gaps
- Timeline layer colours and caption-style previews are set in script as hex values, outside the tokens.
- Rules underneath the system layer still carry old hex values where the layer does not override them.

## Exports

### tokens.css
See `tokens.css` at the project root. The pages carry the same block inline because the server has no route for a shared stylesheet yet.
