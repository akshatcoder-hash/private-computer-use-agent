# Atom: upstream notice

The shipped Atom checkpoint is a derivative artifact: the author selected,
converted, packaged, and evaluated it. It is a 4-bit quantized conversion of
Samsung TinyClick (Florence-2-base lineage) and carries no additional gradient
updates.

This notice covers the upstream components and must travel with any
distributed copy of the model package. It is a compliance artifact, not
marketing copy.

## Upstream components

| component | identifier | declared license | verified |
|---|---|---|---|
| Base architecture and remote code | `microsoft/Florence-2-base` | MIT | 2026-09-26, HF API `license:mit`, `gated:false` |
| Upstream model weights (Samsung TinyClick) | distributed via `Krystianz/TinyClick` rev `0e1356f0b7cfb416099207121f6a766818ab8a66`; same weights mirrored at `kzawistowsk/TinyClick` (`base_model: microsoft/Florence-2-base`) | MIT ("Model was originally shared on MIT license") | 2026-09-26, HF API `license:mit` |
| MLX conversion Atom was converted from | `lokendra77/TinyClick-mlx` rev `c450f599d49c727abee1ee0322aa2ac271a09365` | **none declared** | 2026-09-26, HF API tags contain no `license:*` |

## Points that need a human decision before wider distribution

1. **The copyright holder lines in `LICENSE` are reconstructed, not copied.**
   No first-party copyright line was found: the upstream model card does not
   publish one, and `github.com/SamsungLabs/TinyClick` and the original
   `Samsung/TinyClick` HF repo did not resolve publicly as of 2026-09-26.
   Confirm the exact holder text from a first-party source if one becomes
   available, and replace the lines in `LICENSE`.
2. **The repository Atom was converted from declares no license.**
   `lokendra77/TinyClick-mlx` (rev `c450f599d49c727abee1ee0322aa2ac271a09365`)
   carries no license tag. The MIT basis comes from the upstream TinyClick
   distribution and the `microsoft/Florence-2-base` base, not from that repo.
   This is recorded here deliberately.
3. **MIT permits redistribution, including closed and commercial use.** It
   requires only that the copyright notice and permission notice travel with
   copies and substantial portions, which is what this file and `LICENSE` are
   for. Nothing here obliges the author to publish further source, weights, or
   training data.

## Packaging requirement

Any distributed bundle of the model must contain `LICENSE` and this
`NOTICE.md` alongside the weights, tokenizer, processor, and configs. The
release archive `atom-tinyclick-4bit-v1.tar.gz` carries both under
`licenses/atom/`. The model files themselves (169,049,084 bytes) are
byte-frozen and pinned in `runtime/model-manifest.json`.
