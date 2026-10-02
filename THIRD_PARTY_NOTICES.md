# Third-party notices

r7-Harness is an unofficial integration. Third-party projects remain governed by their own licenses; the r7-Harness MIT license does not replace them.

## Runtime and source dependencies

| Project | Use | License | Source |
|---|---|---|---|
| OMP / oh-my-pi | Upstream terminal agent source patched by this project | MIT; preserved at `licenses/OMP-LICENSE` | https://github.com/can1357/oh-my-pi/tree/v18.4.4 |
| OMP native addon | Prebuilt `@oh-my-pi/pi-natives-linux-x64` package downloaded at install time, checked against `manifest.json` | MIT, same as OMP | https://www.npmjs.com/package/@oh-my-pi/pi-natives-linux-x64 |
| Bun | Pinned build tool/runtime; no binary is committed here | Bun code is MIT; release binaries include LGPL and other licensed components documented upstream | https://github.com/oven-sh/bun/releases/tag/bun-v1.4.0 |
| RTK | Optional external output-compression tool; no binary is committed here | Apache-2.0 | https://github.com/rtk-ai/rtk |
| AutoHotkey v2 | Optional Windows script runtime; no runtime is committed here | GPL-2.0 | https://github.com/AutoHotkey/AutoHotkey |

## Fonts

No font binary is committed in this repository. `fonts/catalog.json` links to official project/download and license pages. This keeps font versions, redistribution terms, and installation choices with each upstream project.

Catalog families include Cascadia Code, JetBrains Mono, Fira Code, Commit Mono, Monaspace, IBM Plex Mono, Source Code Pro, Iosevka, Victor Mono, Ubuntu Mono and a set of OFL fonts from Google Fonts. Proprietary fonts such as Consolas are listed but not linked for download.

Common licenses represented in the catalog include SIL Open Font License 1.1, MIT, and the Ubuntu Font Licence. Refer to the exact catalog entry and linked upstream license before redistribution.

Commit Mono's font files use `LICENSE-FONT` (OFL-1.1); the repository's MIT license applies to its website code. Ubuntu Mono uses the Ubuntu Font Licence 1.0 rather than OFL. Those distinctions are preserved in the catalog.

## Source patch

`patches/omp-18.4.4-r7h3.patch` is a source delta against the exact OMP tag recorded in `manifest.json`. A distributor must preserve the applicable upstream copyright and license notices when distributing a patched build.
