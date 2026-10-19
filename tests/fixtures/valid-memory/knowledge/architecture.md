---
links: ["instructions-contract.md"]
sources: ["PLAN.md"]
---

# Architecture

The main PCW skill reads workspace instructions before using memory; a dedicated mini-skill handles direct requests to edit those instructions. It can work directly or delegate optionally. Ordinary topic materials are automatically indexed using overlapping token windows and the selected embedding source.
